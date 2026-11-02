import base64
import io
import json
import os
import tempfile
import time as system_time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, time
from pathlib import Path
from unittest.mock import Mock, patch

import autosign
import backfill
import woffu
import woffu_config as config
from test_recovery import diary, sign


class ConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "config.json"
        default_patch = patch.object(config, "DEFAULT_CONFIG", self.path)
        default_patch.start()
        self.addCleanup(default_patch.stop)
        env_patch = patch.dict(os.environ, {"WOFFU_CONFIG": ""})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def write(self, values):
        self.path.write_text(json.dumps(values))

    def test_no_config_keeps_existing_schedule_and_recovery(self):
        args = config.parse_args([])
        self.assertEqual((args.start, args.lunch, args.end),
                         (time(7), "13:00-13:30", time(16, 30)))
        self.assertEqual((args.work_hours, args.extra_minutes, args.extra_variance), (8, 10, 5))
        self.assertEqual((args.min_lunch_minutes, args.lunch_variance), (30, 6))
        self.assertEqual((args.recovery, args.recovery_days), ("both", 7))
        self.assertEqual(args.weekdays, (0, 1, 2, 3, 4))

    def test_cli_overrides_weekday_then_file_then_defaults(self):
        self.write({"work_hours": 7.5, "extra_minutes": 0,
                    "weekday_overrides": {"fri": {"work_hours": 6}}})
        args = config.parse_args([])
        self.assertEqual(config.for_day(args, date(2026, 11, 2)).work_hours, 7.5)
        self.assertEqual(config.for_day(args, date(2026, 11, 6)).work_hours, 6)
        args = config.parse_args(["--work-hours", "8", "--extra-minutes", "12"])
        self.assertEqual(config.for_day(args, date(2026, 11, 6)).work_hours, 8)
        self.assertEqual(config.for_day(args, date(2026, 11, 6)).extra_minutes, 12)
        self.assertEqual(config.as_dict(args)["weekday_overrides"]["fri"], {})

    def test_old_close_hours_alias_still_works(self):
        self.assertEqual(config.parse_args(["--close-hours", "7"]).work_hours, 7)

    def test_relative_config_paths_resolve_beside_config(self):
        self.write({"env_file": "credentials", "pause_dir": "pauses"})
        args = config.parse_args([])
        self.assertEqual(args.env_file, self.directory / "credentials")
        self.assertEqual(args.pause_dir, self.directory / "pauses")

    def test_bad_configuration_fails_before_connecting(self):
        cases = [
            {"work_hours": -1}, {"work_hours": float("nan")}, {"extra_minutes": True},
            {"extra_variance": -1}, {"min_lunch_minutes": -1}, {"lunch": "13:30-13:00"},
            {"start": "25:00"}, {"start": "20:00", "end": "23:00"},
            {"lunch": "23:45-23:50"}, {"weekdays": []}, {"weekdays": ["monday"]},
            {"work_hous": 8}, {"recovery": "yes"}, {"recovery_days": 0},
            {"request_timeout": 0}, {"weekday_overrides": {"fri": {"recovery": "off"}}},
        ]
        for values in cases:
            with self.subTest(values=values):
                self.write(values)
                with patch.object(autosign, "Woffu") as client, redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        autosign.main([])
                    self.assertEqual(error.exception.code, 2)
                    client.assert_not_called()

    def test_explicit_missing_or_malformed_config_is_an_error(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            config.parse_args(["--config", str(self.path)])
        self.path.write_text("{broken")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            config.parse_args([])

    def test_show_config_needs_no_credentials_or_network(self):
        self.write({"work_hours": 7, "recovery": "off"})
        output = io.StringIO()
        with patch.object(autosign, "Woffu") as client, redirect_stdout(output):
            autosign.main(["--show-config"])
        self.assertEqual(json.loads(output.getvalue())["work_hours"], 7)
        client.assert_not_called()

    def test_zero_variance_and_custom_minimum_lunch(self):
        self.write({"min_lunch_minutes": 45, "lunch_variance": 0,
                    "extra_variance": 0, "end_early_minutes": 0, "end_late_minutes": 0})
        args = config.parse_args([])
        lunch, end = autosign.daily_times(date(2026, 11, 2), args.lunch, args.end,
            args.min_lunch_minutes, args.lunch_variance, args.end_early_minutes, args.end_late_minutes)
        self.assertEqual(lunch, (time(13), time(13, 45)))
        self.assertEqual(end, time(16, 30))
        self.assertEqual(autosign.work_minutes_for_day(date(2026, 11, 2), 8, 10, 0), 490)
        slots = [{"in": {"shortTrueTime": "08:00:00"},
                  "out": {"shortTrueTime": "13:05:22"}}]
        self.assertIsNone(autosign.next_action(slots, time(13, 50, 21), time(7), lunch, end, 490, 45))
        self.assertEqual(autosign.next_action(slots, time(13, 50, 22), time(7), lunch, end, 490, 45), "sign")
        filled = backfill.plan_day(backfill.parse_slots("08:00-13:00,13:30-17:00"), None, 0, 45)
        self.assertEqual(filled[1]["in"]["time"], "13:45:00")

    def recover(self, client, options):
        with redirect_stdout(io.StringIO()):
            autosign.recover_past(client, date(2026, 11, 2), time(7), "13:00-13:30",
                                  time(16, 30), 8, options=options)

    def test_recovery_off_does_not_fetch_past_diaries(self):
        client = Mock(spec=woffu.Woffu)
        self.recover(client, config.parse_args(["--recovery", "off"]))
        client.diaries.assert_not_called()

    def test_close_only_does_not_fill_and_fill_only_does_not_close(self):
        for mode, slots in (("close", []), ("fill", [{"in": sign("08:00:00", True, 1), "out": None}])):
            with self.subTest(mode=mode):
                client = Mock(spec=woffu.Woffu)
                client.diaries.return_value = [diary()]
                client.diary_slots.return_value = slots
                self.recover(client, config.parse_args(["--recovery", mode]))
                client.put_slots.assert_not_called()

    def test_recovery_uses_historical_weekday_override_and_lookback(self):
        self.write({"recovery_days": 21, "weekday_overrides": {"fri": {"work_hours": 7, "extra_minutes": 0}}})
        client = Mock(spec=woffu.Woffu)
        client.diaries.return_value = [diary()]
        client.diary_slots.return_value = []
        client.put_slots.return_value = 200
        self.recover(client, config.parse_args([]))
        client.diaries.assert_called_once_with("2026-10-12", "2026-11-01")
        self.assertEqual(sum(s["totalMin"] for s in client.put_slots.call_args.args[1]), 420)

    def test_custom_workweek_can_recover_sunday_when_company_marks_it_working(self):
        client = Mock(spec=woffu.Woffu)
        client.diaries.return_value = [diary("2026-11-01", isWeekend=False)]
        client.diary_slots.return_value = []
        client.put_slots.return_value = 200
        self.recover(client, config.parse_args(["--weekdays", "sun,mon,tue,wed,thu"]))
        self.assertEqual(client.put_slots.call_args.args[0], "2026-11-01")


class ManualBackfillTests(unittest.TestCase):
    def test_close_inspects_actual_slots_when_summary_shows_lunch_out(self):
        client = Mock(spec=woffu.Woffu)
        client.diaries.return_value = [diary(**{"in": "08:00:00", "out": "13:00:00"})]
        planned = [woffu.make_slot(woffu.new_sign("08:00:00", True),
                                   woffu.new_sign("16:30:00", False), 1)]
        client.plan_close.return_value = planned
        client.put_slots.return_value = 200
        with patch.object(backfill, "Woffu", return_value=client), \
                patch("sys.argv", ["backfill.py", "close", "--from", "2026-10-30", "--yes"]), \
                patch.object(backfill.time, "sleep"), redirect_stdout(io.StringIO()):
            backfill.main()
        client.plan_close.assert_called_once_with(222, 8, 10)
        client.put_slots.assert_called_once_with("2026-10-30", planned, comments="Existing note")

    def test_fill_does_not_overwrite_entries_missing_from_summary(self):
        client = Mock(spec=woffu.Woffu)
        client.diaries.return_value = [diary()]
        client.diary_slots.return_value = [{"in": sign("08:00:00", True, 1), "out": None}]
        with patch.object(backfill, "Woffu", return_value=client), \
                patch("sys.argv", ["backfill.py", "fill", "--from", "2026-10-30", "--yes"]), \
                redirect_stdout(io.StringIO()):
            backfill.main()
        client.put_slots.assert_not_called()


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        env_patch = patch.dict(os.environ, {}, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def credentials(self, name, user_id, domain="example.woffu.com", expires=None):
        claims = {"UserId": user_id, "exp": expires if expires is not None else system_time.time() + 3600}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        token = f"e30.{payload}.test-signature"
        path = self.directory / name
        path.write_text(f"WOFFU_DOMAIN={domain}\nWOFFU_TOKEN={token}\n")
        return path, token

    def test_separate_credential_files_do_not_contaminate_each_other(self):
        first, _ = self.credentials("one", 1)
        second, _ = self.credentials("two", 2, "https://second.woffu.com/")
        self.assertEqual(woffu.Woffu(first).user_id, 1)
        client = woffu.Woffu(second)
        self.assertEqual(client.user_id, 2)
        self.assertEqual(client.base, "https://second.woffu.com")
        self.assertNotIn("WOFFU_TOKEN", os.environ)

    def test_environment_credentials_take_precedence(self):
        path, _ = self.credentials("one", 1)
        _, second_token = self.credentials("two", 2)
        with patch.dict(os.environ, {"WOFFU_DOMAIN": "override.woffu.com", "WOFFU_TOKEN": second_token}):
            client = woffu.Woffu(path)
        self.assertEqual((client.user_id, client.base), (2, "https://override.woffu.com"))

    def test_env_file_environment_variable_and_request_timeout(self):
        path, _ = self.credentials("one", 1)
        with patch.dict(os.environ, {"WOFFU_ENV_FILE": str(path)}):
            client = woffu.Woffu(request_timeout=9)
        with patch("woffu.requests.get") as get:
            client.live_slots()
        self.assertEqual(get.call_args.kwargs["timeout"], 9)

    def test_expired_malformed_and_missing_credentials_have_clear_errors(self):
        expired, _ = self.credentials("old", 1, expires=1)
        with self.assertRaisesRegex(SystemExit, "expired"):
            woffu.Woffu(expired)
        bad = self.directory / "bad"
        bad.write_text("WOFFU_DOMAIN=example.woffu.com\nWOFFU_TOKEN=private-invalid-value\n")
        with self.assertRaisesRegex(SystemExit, "not a valid Woffu JWT") as error:
            woffu.Woffu(bad)
        self.assertNotIn("private-invalid-value", str(error.exception))
        with self.assertRaisesRegex(SystemExit, "Cannot read credential file"):
            woffu.Woffu(self.directory / "missing")


if __name__ == "__main__":
    unittest.main()
