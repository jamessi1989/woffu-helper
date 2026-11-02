import copy
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import autosign
import woffu


def sign(clock_time, sign_in, sign_id):
    return {"time": clock_time, "signIn": sign_in, "signId": sign_id,
            "signStatus": 0, "signType": 0, "userId": 123,
            "deviceId": 456, "deleted": False}


def diary(day="2026-10-30", **changes):
    return {"date": day, "diaryId": 111, "diarySummaryId": 222,
            "isWeekend": date.fromisoformat(day).weekday() >= 5,
            "isHoliday": False, "isToday": False, "accepted": None,
            "in": "", "out": "", "comments": "Existing note", **changes}


class Monday(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 2)


class Afternoon(datetime):
    @classmethod
    def now(cls):
        return cls(2026, 11, 2, 14)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pause_dir = Path(directory.name)
        pause_patch = patch.object(autosign, "PAUSE_DIR", self.pause_dir)
        pause_patch.start()
        self.addCleanup(pause_patch.stop)
        self.client = Mock(spec=woffu.Woffu)
        self.client.diaries.return_value = [diary()]
        self.client.diary_slots.return_value = []
        self.client.put_slots.return_value = 204
        self.output, self.errors = io.StringIO(), io.StringIO()

    def recover(self, dry_run=False, lunch="13:00-13:30"):
        with redirect_stdout(self.output), redirect_stderr(self.errors):
            autosign.recover_past(self.client, date(2026, 11, 2), time(7),
                                  lunch, time(16, 30), 8, dry_run)

    def test_monday_fills_friday_with_same_schedule_and_minimum_lunch(self):
        self.client.diaries.return_value += [diary("2026-10-31"), diary("2026-11-01")]
        self.recover()
        day, slots = self.client.put_slots.call_args.args
        self.assertEqual(day, "2026-10-30")
        self.assertEqual(self.client.put_slots.call_count, 1)
        self.assertEqual(slots[0]["in"]["time"], "07:00:00")
        lunch, _ = autosign.daily_times(date(2026, 10, 30), "13:00-13:30", time(16, 30))
        self.assertEqual(slots[0]["out"]["time"], lunch[0].isoformat())
        self.assertEqual(slots[1]["in"]["time"], lunch[1].isoformat())
        self.assertEqual(sum(s["totalMin"] for s in slots),
                         autosign.work_minutes_for_day(date(2026, 10, 30), 8))
        self.assertGreaterEqual(woffu.to_min(slots[1]["in"]["time"])
                                - woffu.to_min(slots[0]["out"]["time"]), 30)

    def test_does_not_fill_older_empty_days(self):
        self.client.diaries.return_value = [diary("2026-10-29"), diary()]
        self.recover()
        self.client.diary_slots.assert_called_once_with(222)
        self.assertEqual(self.client.put_slots.call_args.args[0], "2026-10-30")

    def test_fills_single_slot_when_lunch_disabled(self):
        self.recover(lunch="none")
        self.assertEqual(len(self.client.put_slots.call_args.args[1]), 1)

    def test_closes_open_afternoon_even_if_summary_has_lunch_clock_out(self):
        self.client.diaries.return_value = [diary(**{"in": "07:44:34", "out": "13:05:12"})]
        existing = [
            {"in": sign("07:44:34", True, 1), "out": sign("13:05:12", False, 2),
             "motive": {"name": "Existing motive"}},
            {"in": sign("13:35:24", True, 3), "out": None},
        ]
        original = copy.deepcopy(existing)
        self.client.diary_slots.return_value = existing
        self.recover()
        slots = self.client.put_slots.call_args.args[1]
        self.assertEqual(existing, original)
        for before, after in ((existing[0]["in"], slots[0]["in"]),
                              (existing[0]["out"], slots[0]["out"]),
                              (existing[1]["in"], slots[1]["in"])):
            for key, value in before.items():
                self.assertEqual(after[key], value)
            self.assertFalse(after["new"])
        self.assertEqual(slots[0]["motive"], existing[0]["motive"])
        self.assertTrue(slots[1]["out"]["new"])
        self.assertGreaterEqual(sum(s["totalMin"] for s in slots), 485)
        self.assertLessEqual(sum(s["totalMin"] for s in slots), 495)
        self.assertEqual(self.client.put_slots.call_args.kwargs["comments"], "Existing note")

    def test_repeat_run_does_not_rewrite_completed_day(self):
        self.client.put_slots.return_value = 200
        self.recover()
        self.assertIn("recovery fill 2026-10-30", self.output.getvalue())
        self.assertEqual(self.errors.getvalue(), "")
        self.client.diary_slots.return_value = self.client.put_slots.call_args.args[1]
        self.client.put_slots.reset_mock()
        self.recover()
        self.client.put_slots.assert_not_called()

    def test_dry_run_previews_without_writing_and_is_repeatable(self):
        self.recover(dry_run=True)
        first = self.output.getvalue()
        self.assertIn("would fill 2026-10-30", first)
        self.client.put_slots.assert_not_called()
        self.output = io.StringIO()
        self.recover(dry_run=True)
        self.assertEqual(first, self.output.getvalue())

    def test_skips_protected_days(self):
        cases = [
            {"isWeekend": True}, {"isHoliday": True}, {"isToday": True},
            {"absenceEvents": [1]}, {"pendingAbsenceEvents": [1]},
            {"accepted": True}, {"isUserEditable": False},
            {"hasPendingDiaryChangeUser": True}, {"hasPendingDiaryChangeTargetUser": True},
            {"diaryChangeRequestId": 999}, {"date": "2026-11-03"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.client.diaries.return_value = [diary(**changes)]
                self.recover()
        self.client.diary_slots.assert_not_called()
        self.client.put_slots.assert_not_called()

    def test_skips_paused_past_day(self):
        (self.pause_dir / "pause-2026-10-30").touch()
        self.recover()
        self.client.diary_slots.assert_not_called()
        self.client.put_slots.assert_not_called()

    def test_retries_transport_and_http_failures_on_next_run(self):
        for failure in (requests.Timeout("timeout"), requests.HTTPError("unavailable")):
            with self.subTest(failure=type(failure).__name__):
                self.client.put_slots.reset_mock()
                self.client.put_slots.side_effect = [failure, 204]
                self.recover()
                self.recover()
                self.assertEqual(self.client.put_slots.call_count, 2)
        self.assertIn("will retry on the next run", self.errors.getvalue())

    def test_one_bad_day_does_not_prevent_recovery_of_another(self):
        self.client.diaries.return_value = [diary("2026-10-29", **{"in": "08:00"}), diary()]
        self.client.diary_slots.side_effect = [requests.HTTPError("unavailable"), []]
        self.recover()
        self.client.put_slots.assert_called_once()
        self.assertEqual(self.client.put_slots.call_args.args[0], "2026-10-30")

    def run_main(self):
        with patch.object(autosign, "date", Monday), patch.object(autosign, "datetime", Afternoon), \
                patch.object(autosign, "Woffu", return_value=self.client), \
                patch("sys.argv", ["autosign.py", "--pause-dir", str(self.pause_dir)]), \
                redirect_stdout(self.output), redirect_stderr(self.errors):
            autosign.main()

    def test_recovery_failure_does_not_stop_today_signing(self):
        self.client.diaries.side_effect = [requests.Timeout("history timed out"),
                                           [diary("2026-11-02", isToday=True)]]
        self.client.live_slots.return_value = []
        self.run_main()
        self.client.sign.assert_called_once_with()

    def test_today_pause_still_allows_previous_day_recovery(self):
        (self.pause_dir / "pause-2026-11-02").touch()
        self.run_main()
        self.client.put_slots.assert_called_once()
        self.client.live_slots.assert_not_called()
        self.client.sign.assert_not_called()

    def test_pause_all_stops_recovery_and_signing(self):
        (self.pause_dir / "pause-all").touch()
        self.run_main()
        self.client.diaries.assert_not_called()
        self.client.sign.assert_not_called()


class DailyTargetTests(unittest.TestCase):
    def test_extra_time_is_positive_and_repeatable(self):
        for month in range(1, 13):
            for day in range(1, 29):
                when = date(2026, month, day)
                target = autosign.work_minutes_for_day(when, 8)
                self.assertGreaterEqual(target, 485)
                self.assertLessEqual(target, 495)
                self.assertEqual(target, autosign.work_minutes_for_day(when, 8))
        self.assertEqual(autosign.work_minutes_for_day(date(2026, 11, 2), 8, 0), 480)

    def test_late_start_waits_past_fixed_end_to_reach_work_target(self):
        slots = [{"in": {"shortTrueTime": "08:30:00"},
                  "out": {"shortTrueTime": "13:00:00"}},
                 {"in": {"shortTrueTime": "13:35:00"}, "out": None}]
        for now, expected in ((time(16, 30), None), (time(17, 14, 59), None),
                              (time(17, 15), "sign")):
            with self.subTest(now=now):
                self.assertEqual(autosign.next_action(slots, now, time(7),
                    (time(13), time(13, 30)), time(16, 30), 490), expected)

    def test_early_start_finishes_when_target_is_met(self):
        slots = [{"in": {"shortTrueTime": "07:00:00"},
                  "out": {"shortTrueTime": "13:00:00"}},
                 {"in": {"shortTrueTime": "13:30:00"}, "out": None}]
        self.assertEqual(autosign.next_action(slots, time(15, 40), time(7),
                         (time(13), time(13, 30)), time(16, 30), 490), "sign")

    def test_completed_single_slot_is_not_reopened_as_lunch(self):
        slots = [{"in": {"shortTrueTime": "07:00:00"},
                  "out": {"shortTrueTime": "15:10:00"}}]
        self.assertIsNone(autosign.next_action(slots, time(15, 50), time(7),
                          (time(13), time(13, 30)), time(16, 30), 490))


class DiaryApiTests(unittest.TestCase):
    def setUp(self):
        self.client = object.__new__(woffu.Woffu)
        self.client.user_id = 123
        self.client.base = "https://example.invalid"
        self.client.headers = {}
        self.client.request_timeout = 20

    def test_fetches_slots_using_summary_id_and_current_endpoint(self):
        with patch.object(self.client, "_get", return_value={"slots": []}) as get:
            self.assertEqual(self.client.diary_slots(222), [])
        get.assert_called_once_with("/api/svc/core/diariesquery/diarysummaries/222/workday/slots/self")

    def test_write_uses_current_endpoint_and_preserves_local_clock_times(self):
        slots = [woffu.make_slot(woffu.new_sign("08:00:00", True),
                                 woffu.new_sign("16:30:00", False), 1)]
        original = copy.deepcopy(slots)
        response = Mock(status_code=204)
        for day in ("2026-01-30", "2026-07-30"):
            with self.subTest(day=day), patch("woffu.requests.put", return_value=response) as put:
                self.assertEqual(self.client.put_slots(day, slots, comments="Keep this"), 204)
                self.assertEqual(put.call_args.args[0],
                                 "https://example.invalid/api/svc/core/users/123/diarysummaries/workday/slots/self")
                body = put.call_args.kwargs["json"]
                self.assertEqual(body["slots"][0]["in"]["time"], "08:00:00")
                self.assertEqual(body["slots"][0]["out"]["time"], "16:30:00")
                self.assertEqual(body["date"], day)
                self.assertEqual(body["comments"], "Keep this")
                self.assertEqual(put.call_args.kwargs["timeout"], 20)
        self.assertEqual(slots, original)

    def test_rejected_write_raises_for_recovery_to_retry(self):
        response = Mock(status_code=403)
        response.raise_for_status.side_effect = requests.HTTPError("forbidden")
        with patch("woffu.requests.put", return_value=response), self.assertRaises(requests.HTTPError):
            self.client.put_slots("2026-10-30", [])

    def test_decimal_hours_can_close_open_day(self):
        slots = woffu.plan_close_slots([{"in": sign("08:00:00", True, 1), "out": None}], 8.5, 0)
        self.assertEqual(slots[-1]["out"]["time"], "16:30:00")

    def test_ambiguous_layout_and_midnight_overflow_are_not_changed(self):
        self.assertIsNone(woffu.plan_close_slots([
            {"in": sign("08:00:00", True, 1), "out": None},
            {"in": sign("13:30:00", True, 2), "out": None},
        ], 8, 0))
        self.assertIsNone(woffu.plan_close_slots([
            {"in": sign("20:00:00", True, 1), "out": None},
        ], 8, 0))


if __name__ == "__main__":
    unittest.main()
