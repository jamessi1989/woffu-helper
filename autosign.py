#!/usr/bin/env python3
"""Woffu auto clock-in/out. Run it every few minutes on workdays.

Woffu holds the state (today's signs), so each run does just the next step due:
clock in from --start, out at lunch, back in, out at --end. Re-runs, reboots
and manual signs are safe. Also recovers missing days and past clock-outs.

Pause a day: touch ~/.config/woffu/pause-YYYY-MM-DD   (or pause-all)
"""

import json
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import requests

import woffu
import woffu_config as config
from backfill import describe, plan_day
from woffu import Woffu

PAUSE_DIR = Path.home() / ".config" / "woffu"


def hhmm(text):
    return datetime.strptime(text, "%H:%M").time()


def offset(t, rnd, lo, hi):
    return (datetime.combine(date.min, t) + timedelta(minutes=rnd.randint(lo, hi))).time()


def plan_lunch(start, end, rnd, min_minutes=30, variance=6):
    """Jitter lunch times while preserving the configured minimum duration."""
    start, end = offset(start, rnd, 0, variance), offset(end, rnd, 0, variance)
    earliest_end = (datetime.combine(date.min, start)
                    + timedelta(minutes=min_minutes))
    if earliest_end.date() != date.min:
        raise ValueError("lunch must fit before midnight")
    return start, max(end, earliest_end.time())


def daily_times(day, lunch_text, end, min_lunch_minutes=30, lunch_variance=6,
                end_early_minutes=5, end_late_minutes=10):
    """Use the same planned times for live signing and next-day recovery."""
    rnd = random.Random(day.isoformat())
    lunch = None
    if lunch_text != "none":
        a, b = map(hhmm, lunch_text.split("-"))
        lunch = plan_lunch(a, b, rnd, min_lunch_minutes, lunch_variance)
    return lunch, offset(end, rnd, -end_early_minutes, end_late_minutes)


def work_minutes_for_day(day, hours, extra_minutes=10, extra_variance=5):
    """Keep a small, repeatable amount of extra time above the daily target."""
    extra = random.Random(f"{day.isoformat()}:extra").randint(
        max(1, extra_minutes - extra_variance), extra_minutes + extra_variance) if extra_minutes else 0
    return round(hours * 60) + extra


def fill_slots(start, lunch, minutes, min_lunch_minutes=30):
    """Build a missing day with the target worked time and its lunch break."""
    start_min = start.hour * 60 + start.minute
    finish_min = start_min + minutes
    if lunch and start < lunch[0] and finish_min > lunch[0].hour * 60 + lunch[0].minute:
        finish_min += ((lunch[1].hour * 60 + lunch[1].minute)
                       - (lunch[0].hour * 60 + lunch[0].minute))
        bounds = [(start.strftime("%H:%M"), lunch[0].strftime("%H:%M")),
                  (lunch[1].strftime("%H:%M"), woffu.to_time(finish_min))]
    else:
        bounds = [(start.strftime("%H:%M"), woffu.to_time(finish_min))]
    if finish_min >= 24 * 60:
        raise ValueError("the daily target would run past midnight")
    return plan_day(bounds, None, 0, min_lunch_minutes)


def worked_time(slots, now):
    """Actual worked time today, excluding every recorded break."""
    total = timedelta()
    for slot in slots:
        start = time.fromisoformat(slot["in"]["shortTrueTime"])
        end = time.fromisoformat(slot["out"]["shortTrueTime"]) if slot.get("out") else now
        total += datetime.combine(date.min, end) - datetime.combine(date.min, start)
    return total


def recover_past(client, today, start, lunch_text, end, hours, dry_run=False, extra_minutes=10,
                 options=None):
    """Recover missing days and clock-outs according to the configured policy."""
    options = options or config.defaults(start=start, lunch=lunch_text, end=end,
                                          work_hours=hours, extra_minutes=extra_minutes,
                                          pause_dir=PAUSE_DIR)
    if options.recovery == "off":
        return
    try:
        past = client.diaries((today - timedelta(days=options.recovery_days)).isoformat(),
                              (today - timedelta(days=1)).isoformat())
    except requests.RequestException as exc:
        print(f"recovery failed: {exc}; will retry on the next run", file=sys.stderr)
        return
    workdays = sorted((d for d in past
                       if d["date"] < today.isoformat() and not d.get("isToday")
                       and date.fromisoformat(d["date"]).weekday() in options.weekdays
                       and not d["isWeekend"] and not d["isHoliday"]
                       and not woffu.has_absence(d)
                       and not (options.pause_dir / f"pause-{d['date']}").exists()),
                      key=lambda d: d["date"])
    if not workdays:
        return
    previous_day = workdays[-1]["date"]
    for d in workdays:
        day = d["date"]
        if (d.get("accepted") or d.get("isUserEditable") is False
                or d.get("hasPendingDiaryChangeUser")
                or d.get("hasPendingDiaryChangeTargetUser")
                or d.get("diaryChangeRequestId")):
            continue
        if day != previous_day and woffu.needs_filling(d):
            continue
        try:
            # A summary can show the lunch clock-out even when the final
            # afternoon slot is open, so always inspect the actual slots.
            existing = client.diary_slots(d["diarySummaryId"])
            day_date = date.fromisoformat(day)
            settings = config.for_day(options, day_date)
            minutes = work_minutes_for_day(day_date, settings.work_hours,
                                          settings.extra_minutes, settings.extra_variance)
            if not existing and day == previous_day and woffu.needs_filling(d):
                if options.recovery not in ("fill", "both"):
                    continue
                lunch, _ = daily_times(day_date, settings.lunch, settings.end,
                    settings.min_lunch_minutes, settings.lunch_variance,
                    settings.end_early_minutes, settings.end_late_minutes)
                slots = fill_slots(settings.start, lunch, minutes, settings.min_lunch_minutes)
                action = "fill"
            else:
                if options.recovery not in ("close", "both"):
                    continue
                slots = woffu.plan_close_slots(existing, minutes / 60, 0)
                action = "close"
            if not slots:
                continue
            if dry_run:
                print(f"would {action} {day}: {describe(slots)}")
            else:
                status = client.put_slots(day, slots, comments=d.get("comments") or "")
                if not 200 <= status < 300:
                    print(f"recovery failed for {day}: HTTP {status}; will retry on the next run",
                          file=sys.stderr)
                else:
                    print(f"recovery {action} {day}: {describe(slots)} ({status})")
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"recovery failed for {day}: {exc}; will retry on the next run", file=sys.stderr)


def next_action(slots, now, start, lunch, end, target_minutes=None, min_lunch_minutes=30):
    """'sign' if a clock in/out is due now, else None."""
    state = "idle" if not slots else ("out" if slots[-1].get("out") else "in")
    if state == "idle":
        return "sign" if start <= now < end else None
    if target_minutes is not None:
        if worked_time(slots, now) >= timedelta(minutes=target_minutes):
            return "sign" if state == "in" else None
    if state == "in":
        if target_minutes is None and now >= end:
            return "sign"
        if lunch and len(slots) == 1 and lunch[0] <= now < lunch[1]:
            first_in = hhmm(slots[0]["in"]["shortTrueTime"][:5])
            return "sign" if first_in < lunch[0] else None
        return None
    # Out: only come back from lunch, never re-open a finished day.
    if lunch and len(slots) == 1 and lunch[1] <= now < end:
        # A late timer run can start lunch after the planned time. Preserve
        # seconds so a break cannot be rounded up to the minimum duration.
        last_out = time.fromisoformat(slots[0]["out"]["shortTrueTime"])
        elapsed = datetime.combine(date.min, now) - datetime.combine(date.min, last_out)
        return "sign" if elapsed >= timedelta(minutes=min_lunch_minutes) else None
    return None


def main(argv=None):
    args = config.parse_args(argv, description=__doc__)
    if args.show_config:
        print(json.dumps(config.as_dict(args), indent=2))
        return
    today = date.today()
    if today.weekday() not in args.weekdays:
        return
    if (args.pause_dir / "pause-all").exists():
        print("paused")
        return

    settings = config.for_day(args, today)
    lunch, end = daily_times(today, settings.lunch, settings.end,
        settings.min_lunch_minutes, settings.lunch_variance,
        settings.end_early_minutes, settings.end_late_minutes)
    client = Woffu(env_file=args.env_file, request_timeout=args.request_timeout)
    recover_past(client, today, args.start, args.lunch, args.end,
                 args.work_hours, args.dry_run, args.extra_minutes, options=args)
    if (args.pause_dir / f"pause-{today}").exists():
        print("paused")
        return
    diary = client.diaries(today.isoformat(), today.isoformat())[0]
    if diary.get("isWeekend") or diary["isHoliday"] or woffu.has_absence(diary):
        return

    slots = client.live_slots()
    now = datetime.now().time().replace(microsecond=0)
    target = work_minutes_for_day(today, settings.work_hours, settings.extra_minutes, settings.extra_variance)
    action = next_action(slots, now, settings.start, lunch, end, target, settings.min_lunch_minutes)
    lunch_txt = f"{lunch[0]}-{lunch[1]}" if lunch else "none"
    finish_txt = "unknown"
    if slots and not slots[-1].get("out"):
        remaining = max(timedelta(minutes=target) - worked_time(slots, now), timedelta())
        finish_txt = (datetime.combine(today, now) + remaining).strftime("%H:%M:%S")
    print(f"{now} signs={len(slots)} lunch={lunch_txt} target={target // 60}h{target % 60:02d} "
          f"finish={finish_txt} -> {action or 'nothing'}")
    if action and not args.dry_run:
        client.sign()


def cli():
    try:
        main()
    except requests.RequestException as e:
        print(f"woffu error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli()
