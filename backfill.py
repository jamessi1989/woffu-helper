#!/usr/bin/env python3
"""Backfill Woffu diary days.

  fill   write a schedule into workdays that have nothing recorded
  close  add a clock-out to past days that were clocked in but never out

Weekends, holidays, absences, locked days and pending edits are skipped.
Filling also skips existing entries. Always try --dry-run first.
"""

import argparse
import random
import sys
import time
from datetime import date, datetime, timedelta

import woffu
import requests
from woffu_config import clock
from woffu import Woffu, jitter, make_slot, new_sign, to_min, to_time


def parse_slots(text):
    """'08:00-13:00,13:30-17:00' -> [('08:00', '13:00'), ('13:30', '17:00')]"""
    try:
        slots = []
        for part in text.split(","):
            a, b = map(clock, part.split("-"))
            if a >= b or (slots and a < clock(slots[-1][1])):
                raise ValueError("work slots must be ordered, non-overlapping, and finish after they start")
            slots.append((a.strftime("%H:%M"), b.strftime("%H:%M")))
        return slots
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def parse_starts(text):
    """'07:00:45,08:00:40,08:30:15' -> (['07:00', ...], [45, ...])"""
    pairs = [part.rsplit(":", 1) if part.count(":") == 2 else (part, "1")
             for part in text.split(",")]
    try:
        starts = [clock(p[0]).strftime("%H:%M") for p in pairs]
        weights = [int(p[1]) for p in pairs]
        if any(weight <= 0 for weight in weights):
            raise ValueError("start weights must be positive")
        return starts, weights
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def plan_day(schedule, starts, variance, min_lunch_minutes=30):
    """Pick a start (if --starts), shift the day's first in / last out by it
    (breaks stay put), then jitter boundaries and enforce the minimum break."""
    if variance < 0 or min_lunch_minutes < 0:
        raise ValueError("variance and minimum lunch must be nonnegative")
    bounds = [list(slot) for slot in schedule]
    if starts:
        start = random.choices(*starts)[0]
        shift = to_min(start) - to_min(bounds[0][0])
        bounds[0][0] = to_time(to_min(bounds[0][0]) + shift)[:5]
        bounds[-1][1] = to_time(to_min(bounds[-1][1]) + shift)[:5]
    slots = []
    for i, (a, b) in enumerate(bounds, 1):
        a, b = jitter(a, variance), jitter(b, variance)
        if slots:
            earliest_in = to_min(slots[-1]["out"]["time"]) + min_lunch_minutes
            a = to_time(max(to_min(a), earliest_in))
        if not 0 <= to_min(a) < to_min(b) < 24 * 60:
            raise ValueError(f"Slot {i} must end after it starts with at least "
                             f"{min_lunch_minutes} minutes between work slots, within one calendar day")
        slots.append(make_slot(new_sign(a, True), new_sign(b, False), i))
    return slots


def describe(slots):
    times = "  ".join(f"{s['in']['time'][:5]}-{s['out']['time'][:5]}" for s in slots)
    total = sum(s["totalMin"] for s in slots)
    return f"{times}  ({total // 60}h{total % 60:02d})"


def run(client, plans, args):
    for _, day, slots in plans:
        dow = datetime.strptime(day, "%Y-%m-%d").strftime("%a")
        print(f"  {day} ({dow})  {describe(slots)}")
    if not plans:
        print("Nothing to do.")
        return
    if args.dry_run:
        print(f"\nDry run: {len(plans)} day(s), nothing written. "
              "Times are re-rolled on the real run.")
        return
    if not args.yes and input(f"\nWrite {len(plans)} day(s)? [y/N] ").lower() != "y":
        print("Aborted.")
        return
    ok = 0
    for comments, day, slots in plans:
        status = client.put_slots(day, slots, comments=comments)
        print(f"  {day}  {describe(slots)}  {'OK' if 200 <= status < 300 else f'FAIL ({status})'}")
        ok += 200 <= status < 300
        time.sleep(0.3)
    print(f"\nDone: {ok}/{len(plans)} written (past days may show as pending approval).")


def main():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("fill", "close"):
        p = sub.add_parser(name)
        p.add_argument("--from", dest="frm", required=True, help="YYYY-MM-DD")
        p.add_argument("--to", default=yesterday, help="YYYY-MM-DD, inclusive (default: yesterday)")
        p.add_argument("--variance", type=int, default=10, help="± minutes per time (default 10)")
        p.add_argument("--skip", nargs="*", default=[], help="dates to leave alone")
        p.add_argument("--limit", type=int, help="only write the first N days")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--yes", action="store_true", help="no confirmation prompt")
        p.add_argument("--env-file", help="credential file (environment variables take precedence)")
    fill = sub.choices["fill"]
    fill.add_argument("--slots", type=parse_slots, default="08:00-13:00,13:30-17:00",
                      help="work slots (default: 08:00-13:00,13:30-17:00)")
    fill.add_argument("--starts", type=parse_starts,
                      help="rotate the day's start, e.g. 07:00:45,08:00:40,08:30:15 "
                           "(time:weight); the end moves with it")
    fill.add_argument("--min-lunch-minutes", type=int, default=30,
                      help="minimum break between work slots (default 30)")
    sub.choices["close"].add_argument("--hours", type=float, default=8,
                                      help="target worked hours for the day (default 8)")
    args = parser.parse_args()

    try:
        if date.fromisoformat(args.frm) > date.fromisoformat(args.to):
            parser.error("--from must not be after --to")
    except ValueError:
        parser.error("dates must use YYYY-MM-DD")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.cmd == "close" and not 0 < args.hours < 24:
        parser.error("--hours must be between 0 and 24")
    if args.variance < 0 or getattr(args, "min_lunch_minutes", 0) < 0:
        parser.error("variance and minimum lunch must be nonnegative")
    client = Woffu(env_file=args.env_file)
    print(f"Fetching diaries {args.frm} .. {args.to}")
    days = [d for d in client.diaries(args.frm, args.to) if d["date"] not in args.skip]

    plans = []
    if args.cmd == "fill":
        for d in filter(woffu.needs_filling, days):
            if client.diary_slots(d["diarySummaryId"]):
                continue
            plans.append((d.get("comments") or "", d["date"],
                          plan_day(args.slots, args.starts, args.variance, args.min_lunch_minutes)))
    else:
        for d in filter(woffu.is_editable, days):
            if woffu.needs_filling(d):
                continue
            slots = client.plan_close(d["diarySummaryId"], args.hours, args.variance)
            if slots is None:
                print(f"  {d['date']}  skipped (closed or unexpected slot layout)")
            else:
                plans.append((d.get("comments") or "", d["date"], slots))

    run(client, plans[:args.limit] if args.limit else plans, args)


def cli():
    try:
        main()
    except (requests.RequestException, ValueError) as exc:
        print(f"woffu error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli()
