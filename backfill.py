#!/usr/bin/env python3
"""Backfill Woffu diary days.

  fill   write a schedule into workdays that have nothing recorded
  close  add a clock-out to past days that were clocked in but never out

Weekends, holidays, approved/pending absences and days that already have
signs are always skipped. Always try --dry-run first.
"""

import argparse
import random
import time
from datetime import date, datetime, timedelta

import woffu
from woffu import Woffu, jitter, make_slot, new_sign, to_min, to_time


def parse_slots(text):
    """'08:00-13:00,13:30-17:00' -> [('08:00', '13:00'), ('13:30', '17:00')]"""
    return [tuple(part.split("-")) for part in text.split(",")]


def parse_starts(text):
    """'07:00:45,08:00:40,08:30:15' -> (['07:00', ...], [45, ...])"""
    pairs = [part.rsplit(":", 1) if part.count(":") == 2 else (part, "1")
             for part in text.split(",")]
    return [p[0] for p in pairs], [int(p[1]) for p in pairs]


def plan_day(schedule, starts, variance):
    """Pick a start (if --starts), shift the day's first in / last out by it
    (breaks stay put), then jitter every boundary."""
    bounds = [list(slot) for slot in schedule]
    if starts:
        start = random.choices(*starts)[0]
        shift = to_min(start) - to_min(bounds[0][0])
        bounds[0][0] = to_time(to_min(bounds[0][0]) + shift)[:5]
        bounds[-1][1] = to_time(to_min(bounds[-1][1]) + shift)[:5]
    return [make_slot(new_sign(jitter(a, variance), True),
                      new_sign(jitter(b, variance), False), i)
            for i, (a, b) in enumerate(bounds, 1)]


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
    for diary_id, day, slots in plans:
        status = client.put_slots(diary_id, day, slots)
        print(f"  {day}  {describe(slots)}  {'OK' if status == 204 else f'FAIL ({status})'}")
        ok += status == 204
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
    fill = sub.choices["fill"]
    fill.add_argument("--slots", type=parse_slots, default="08:00-13:00,13:30-17:00",
                      help="work slots (default: 08:00-13:00,13:30-17:00)")
    fill.add_argument("--starts", type=parse_starts,
                      help="rotate the day's start, e.g. 07:00:45,08:00:40,08:30:15 "
                           "(time:weight); the end moves with it")
    sub.choices["close"].add_argument("--hours", type=float, default=8,
                                      help="target worked hours for the day (default 8)")
    args = parser.parse_args()

    client = Woffu()
    print(f"Fetching diaries {args.frm} .. {args.to}")
    days = [d for d in client.diaries(args.frm, args.to) if d["date"] not in args.skip]

    plans = []
    if args.cmd == "fill":
        for d in filter(woffu.needs_filling, days):
            plans.append((d["diaryId"], d["date"],
                          plan_day(args.slots, args.starts, args.variance)))
    else:
        for d in filter(woffu.is_open, days):
            slots = client.plan_close(d["diaryId"], args.hours, args.variance)
            if slots is None:
                print(f"  {d['date']}  skipped (unexpected slot layout)")
            else:
                plans.append((d["diaryId"], d["date"], slots))

    run(client, plans[:args.limit] if args.limit else plans, args)


if __name__ == "__main__":
    main()
