#!/usr/bin/env python3
"""Woffu auto clock-in/out. Run it every few minutes on workdays.

Woffu holds the state (today's signs), so each run does just the next step due:
clock in from --start, out at lunch, back in, out at --end. Re-runs, reboots
and manual signs are safe. Also closes past days left without a clock-out.

Pause a day: touch ~/.config/woffu/pause-YYYY-MM-DD   (or pause-all)
"""

import argparse
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import requests

import woffu
from woffu import Woffu

PAUSE_DIR = Path.home() / ".config" / "woffu"


def hhmm(text):
    return datetime.strptime(text, "%H:%M").time()


def offset(t, rnd, lo, hi):
    return (datetime.combine(date.min, t) + timedelta(minutes=rnd.randint(lo, hi))).time()


def next_action(slots, now, start, lunch, end):
    """'sign' if a clock in/out is due now, else None."""
    state = "idle" if not slots else ("out" if slots[-1].get("out") else "in")
    if state == "idle":
        return "sign" if start <= now < end else None
    if state == "in":
        if now >= end:
            return "sign"
        if lunch and len(slots) == 1 and lunch[0] <= now < lunch[1]:
            first_in = hhmm(slots[0]["in"]["shortTrueTime"][:5])
            return "sign" if first_in < lunch[0] else None
        return None
    # Out: only come back from lunch, never re-open a finished day.
    return "sign" if lunch and len(slots) == 1 and lunch[1] <= now < end else None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=hhmm, default="07:00",
                        help="earliest clock-in (default 07:00)")
    parser.add_argument("--lunch", default="13:00-13:30",
                        help="lunch break, or 'none' (default 13:00-13:30)")
    parser.add_argument("--end", type=hhmm, default="16:30", help="clock-out (default 16:30)")
    parser.add_argument("--close-hours", type=float, default=8,
                        help="hours to credit when closing a forgotten day (default 8)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    today = date.today()
    now = datetime.now().time().replace(microsecond=0)
    if today.weekday() >= 5:
        return
    if any((PAUSE_DIR / f).exists() for f in ("pause-all", f"pause-{today}")):
        print("paused")
        return

    client = Woffu()
    diary = client.diaries(today.isoformat(), today.isoformat())[0]
    if diary["isHoliday"] or woffu.has_absence(diary):
        return

    if not args.dry_run:
        past = client.diaries((today - timedelta(days=7)).isoformat(),
                              (today - timedelta(days=1)).isoformat())
        for d in filter(woffu.is_open, past):
            slots = client.plan_close(d["diaryId"], args.close_hours, 10)
            if slots:
                status = client.put_slots(d["diaryId"], d["date"], slots)
                print(f"closed {d['date']} at {slots[-1]['out']['time']} ({status})")

    # Jitter is seeded by the date so every run of the day agrees on the times.
    rnd = random.Random(today.isoformat())
    lunch = None
    if args.lunch != "none":
        a, b = map(hhmm, args.lunch.split("-"))
        lunch = (offset(a, rnd, 0, 6), offset(b, rnd, 0, 6))
    end = offset(args.end, rnd, -5, 10)

    slots = client.live_slots()
    action = next_action(slots, now, args.start, lunch, end)
    lunch_txt = f"{lunch[0]}-{lunch[1]}" if lunch else "none"
    print(f"{now} signs={len(slots)} lunch={lunch_txt} end={end} -> {action or 'nothing'}")
    if action and not args.dry_run:
        client.sign()


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        print(f"woffu error: {e}", file=sys.stderr)
        sys.exit(1)
