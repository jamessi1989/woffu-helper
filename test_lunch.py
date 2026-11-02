import random
import unittest
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from autosign import next_action, plan_lunch
from backfill import parse_slots, parse_starts, plan_day
from woffu import to_min


class AutosignLunchTests(unittest.TestCase):
    def test_daily_lunch_is_at_least_30_minutes_and_repeatable(self):
        for day in range(366):
            seed = (date(2026, 1, 1) + timedelta(days=day)).isoformat()
            with self.subTest(day=seed):
                lunch = plan_lunch(time(13), time(13, 30), random.Random(seed))
                duration = (datetime.combine(date.min, lunch[1])
                            - datetime.combine(date.min, lunch[0]))
                self.assertGreaterEqual(duration, timedelta(minutes=30))
                self.assertLessEqual(duration, timedelta(minutes=36))
                self.assertEqual(lunch, plan_lunch(time(13), time(13, 30), random.Random(seed)))

    def test_short_configured_lunch_is_extended(self):
        start, end = plan_lunch(time(13), time(13, 10), random.Random(0))
        self.assertEqual(datetime.combine(date.min, end) - datetime.combine(date.min, start),
                         timedelta(minutes=30))

    def test_late_clock_out_waits_full_30_minutes_including_seconds(self):
        slots = [{"in": {"shortTrueTime": "08:00:00"},
                  "out": {"shortTrueTime": "13:10:22"}}]
        for now, expected in ((time(13, 30), None), (time(13, 40, 21), None),
                              (time(13, 40, 22), "sign"), (time(13, 45), "sign")):
            with self.subTest(now=now):
                self.assertEqual(next_action(slots, now, time(7),
                                             (time(13), time(13, 30)), time(16, 30)), expected)

    def test_longer_scheduled_lunch_is_respected(self):
        slots = [{"in": {"shortTrueTime": "08:00:00"},
                  "out": {"shortTrueTime": "13:00:00"}}]
        lunch = (time(13), time(14))
        self.assertIsNone(next_action(slots, time(13, 45), time(7), lunch, time(16, 30)))
        self.assertEqual(next_action(slots, time(14), time(7), lunch, time(16, 30)), "sign")

    def test_disabled_lunch_or_finished_day_does_not_clock_back_in(self):
        slots = [{"in": {"shortTrueTime": "08:00:00"},
                  "out": {"shortTrueTime": "13:10:22"}}]
        self.assertIsNone(next_action(slots, time(14), time(7), None, time(16, 30)))
        self.assertIsNone(next_action(slots, time(16, 30), time(7),
                                      (time(13), time(13, 30)), time(16, 30)))
        slots.append({"in": {"shortTrueTime": "13:45:00"},
                      "out": {"shortTrueTime": "15:00:00"}})
        self.assertIsNone(next_action(slots, time(15, 30), time(7),
                                      (time(13), time(13, 30)), time(16, 30)))


class BackfillLunchTests(unittest.TestCase):
    def test_jitter_cannot_shorten_lunch_below_30_minutes(self):
        schedule = parse_slots("08:00-13:00,13:30-17:00")
        for lunch_out_offset in range(-10, 11):
            for lunch_in_offset in range(-10, 11):
                with self.subTest(out_offset=lunch_out_offset, in_offset=lunch_in_offset):
                    with patch("woffu.random.randint", side_effect=[
                            0, lunch_out_offset, lunch_in_offset, 0]):
                        slots = plan_day(schedule, None, 10)
                    gap = to_min(slots[1]["in"]["time"]) - to_min(slots[0]["out"]["time"])
                    self.assertGreaterEqual(gap, 30)
                    self.assertLessEqual(gap, 50)
                    self.assertEqual(slots[0]["in"]["time"], "08:00:00")
                    self.assertEqual(slots[-1]["out"]["time"], "17:00:00")
                    for slot in slots:
                        self.assertEqual(slot["totalMin"],
                                         to_min(slot["out"]["time"]) - to_min(slot["in"]["time"]))

    def test_short_configured_break_is_extended_with_zero_variance(self):
        slots = plan_day(parse_slots("08:00-13:00,13:10-17:00"), None, 0)
        self.assertEqual(slots[1]["in"]["time"], "13:30:00")

    def test_longer_break_and_rotated_start_are_preserved(self):
        slots = plan_day(parse_slots("08:00-13:00,14:00-17:00"),
                         parse_starts("07:00:1"), 0)
        self.assertEqual([(s["in"]["time"], s["out"]["time"]) for s in slots],
                         [("07:00:00", "13:00:00"), ("14:00:00", "16:00:00")])

    def test_single_slot_does_not_add_lunch(self):
        slots = plan_day(parse_slots("07:00-15:00"), None, 0)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["totalMin"], 480)

    def test_break_cannot_create_an_invalid_work_slot(self):
        with self.assertRaisesRegex(ValueError, "Slot 2 must end after it starts"):
            plan_day(parse_slots("08:00-13:00,13:10-13:20"), None, 0)


if __name__ == "__main__":
    unittest.main()
