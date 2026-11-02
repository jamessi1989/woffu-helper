"""MCP repair invariants and actual SDK/stdio integration, without Woffu credentials."""

import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import woffu
import woffu_config as config
from woffu_mcp import HelperError, WoffuTools, create_server

try:
    from mcp import Client, StdioServerParameters
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


ROOT = Path(__file__).resolve().parent
DAY = "2026-10-30"


class Monday(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 2)


def diary(**changes):
    return {"date": DAY, "diarySummaryId": 222, "isWeekend": False,
            "isHoliday": False, "isToday": False, "in": "", "out": "",
            "comments": "Keep this note", **changes}


def sign(value, incoming, ident):
    return {"time": value, "signIn": incoming, "signId": ident,
            "signStatus": 0, "signType": 0, "userId": 42, "iP": "private-address"}


def open_slots():
    return [{"in": sign("09:00:02", True, 1), "out": sign("13:00:02", False, 2),
             "motive": {"id": 123}},
            {"in": sign("13:35:09", True, 3), "out": None}]


class RepairTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.options = config.defaults(start="09:00", end="17:30", extra_minutes=0,
            lunch_variance=0, end_early_minutes=0, end_late_minutes=0,
            pause_dir=directory.name)
        self.client = Mock(spec=woffu.Woffu)
        self.client.base, self.client.user_id = "https://example.woffu.com", 42
        self.client.diaries.return_value = [diary()]
        self.client.diary_slots.return_value = []
        self.client.put_slots.return_value = 204
        self.service = WoffuTools(self.options, True, lambda: self.client)
        today = patch("woffu_mcp.date", Monday)
        today.start()
        self.addCleanup(today.stop)

    def test_fill_preview_has_exact_times_minimum_lunch_and_no_write(self):
        plan = self.service.preview_fill(DAY)
        self.assertEqual(plan["after"]["slots"], [
            {"in": "09:00:00", "out": "13:00:00"},
            {"in": "13:30:00", "out": "17:30:00"}])
        self.assertEqual(plan["after"]["worked_seconds"], 8 * 3600)
        self.assertEqual(plan["after"]["break_seconds"], [1800])
        self.client.put_slots.assert_not_called()

    def test_apply_uses_previewed_payload_and_preserves_comments(self):
        plan = self.service.preview_fill(DAY)
        payload = copy.deepcopy(self.service.plans[plan["plan_id"]]["slots"])
        result = self.service.apply_plan(plan["plan_id"])
        self.client.put_slots.assert_called_once_with(DAY, payload, comments="Keep this note")
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["after"], plan["after"])
        with self.assertRaisesRegex(HelperError, "used plan"):
            self.service.apply_plan(plan["plan_id"])

    def test_concurrent_apply_cannot_submit_same_plan_twice(self):
        plan = self.service.preview_fill(DAY)
        def apply():
            try:
                return self.service.apply_plan(plan["plan_id"])["status"]
            except HelperError:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: apply(), range(2)))
        self.assertCountEqual(results, ["submitted", "rejected"])
        self.assertEqual(self.client.put_slots.call_count, 1)

    def test_changed_entries_comments_or_account_reject_preview(self):
        for changed in ("slots", "comments", "account"):
            with self.subTest(changed=changed):
                self.client.diaries.return_value = [diary()]
                self.client.diary_slots.return_value = []
                self.client.user_id = 42
                plan = self.service.preview_fill(DAY)
                if changed == "slots":
                    self.client.diary_slots.return_value = open_slots()
                elif changed == "comments":
                    self.client.diaries.return_value = [diary(comments="A new note")]
                else:
                    self.client.user_id = 43
                with self.assertRaisesRegex(HelperError, "changed since preview"):
                    self.service.apply_plan(plan["plan_id"])
        self.client.put_slots.assert_not_called()

    def test_blocked_days_cannot_be_previewed(self):
        cases = [dict(isWeekend=True), dict(isHoliday=True), dict(accepted=True),
            dict(isUserEditable=False), dict(absenceEvents=[{}]),
            dict(pendingAbsenceEvents=[{}]), dict(hasPendingDiaryChangeUser=True),
            dict(hasPendingDiaryChangeTargetUser=True), dict(diaryChangeRequestId=1),
            dict(isToday=True), dict(isHoliday=None), dict(date="2026-11-03")]
        for changes in cases:
            with self.subTest(changes=changes):
                record = diary(**changes)
                self.client.diaries.return_value = [record]
                with self.assertRaisesRegex(HelperError, "cannot be changed"):
                    self.service.preview_fill(record["date"])
        self.client.put_slots.assert_not_called()

    def test_eligibility_and_pause_are_rechecked_on_apply(self):
        for flag in ("accepted", "pause-all", f"pause-{DAY}"):
            with self.subTest(flag=flag):
                self.client.diaries.return_value = [diary()]
                plan = self.service.preview_fill(DAY)
                if flag == "accepted":
                    self.client.diaries.return_value = [diary(accepted=True)]
                else:
                    (self.options.pause_dir / flag).touch()
                with self.assertRaisesRegex(HelperError, "cannot be changed"):
                    self.service.apply_plan(plan["plan_id"])
                if flag != "accepted":
                    (self.options.pause_dir / flag).unlink()
        self.client.put_slots.assert_not_called()

    def test_fill_checks_actual_entries_and_summary(self):
        self.client.diary_slots.return_value = open_slots()
        with self.assertRaisesRegex(HelperError, "already has recorded"):
            self.service.preview_fill(DAY)
        self.client.diary_slots.return_value = []
        self.client.diaries.return_value = [diary(**{"in": "09:00:00"})]
        with self.assertRaisesRegex(HelperError, "already has recorded"):
            self.service.preview_fill(DAY)

    def test_explicit_times_are_exact_and_short_break_is_rejected(self):
        plan = self.service.preview_fill(DAY, "08:00-12:00,13:00-17:00")
        self.assertEqual(plan["after"]["slots"][1]["in"], "13:00:00")
        for text in ("09:00-13:00,13:15-17:15", "22:00-06:00", "nonsense"):
            with self.subTest(text=text), self.assertRaises(HelperError):
                self.service.preview_fill(DAY, text)

    def test_configured_weekday_override_controls_default_plan(self):
        self.options._day_overrides = {4: config.validate({**config.DEFAULTS,
            "start": "09:00", "end": "17:30", "work_hours": 6,
            "extra_minutes": 10, "extra_variance": 0, "lunch": "none"})}
        plan = self.service.preview_fill(DAY)
        self.assertEqual(plan["after"]["worked_seconds"], 370 * 60)
        self.assertEqual(len(plan["after"]["slots"]), 1)

    def test_close_preserves_original_signs_and_slot_motive(self):
        original = open_slots()
        self.client.diaries.return_value = [diary(**{"in": "09:00:02", "out": "13:00:02"})]
        self.client.diary_slots.return_value = copy.deepcopy(original)
        plan = self.service.preview_close(DAY, 8)
        self.assertEqual(plan["after"]["slots"][-1]["out"], "17:35:09")
        self.assertNotIn("private-address", json.dumps(plan))
        self.service.apply_plan(plan["plan_id"])
        written = self.client.put_slots.call_args.args[1]
        for i, key in ((0, "in"), (0, "out"), (1, "in")):
            for name, value in original[i][key].items():
                self.assertEqual(written[i][key][name], value)
        self.assertEqual(written[0]["motive"], original[0]["motive"])
        self.assertTrue(written[-1]["out"]["new"])
        self.assertEqual(self.client.diary_slots.return_value, original)

    def test_close_rejects_closed_ambiguous_and_overnight_slots(self):
        for slots in ([], [open_slots()[1], open_slots()[1]],
                      [{"in": sign("23:00:00", True, 1), "out": None}],
                      [open_slots()[0]]):
            with self.subTest(slots=slots), self.assertRaises(HelperError):
                self.client.diary_slots.return_value = slots
                self.service.preview_close(DAY, 8)

    def test_close_rejects_invalid_work_targets(self):
        self.client.diary_slots.return_value = open_slots()
        for hours in (float("nan"), float("inf"), -1, 0, 24, True):
            with self.subTest(hours=hours), self.assertRaises(HelperError):
                self.service.preview_close(DAY, hours)

    def test_expired_and_unknown_plans_never_write(self):
        plan = self.service.preview_fill(DAY)
        self.service.plans[plan["plan_id"]]["expires"] = 0
        for plan_id in (plan["plan_id"], "unknown"):
            with self.assertRaisesRegex(HelperError, "expired or used"):
                self.service.apply_plan(plan_id)
        self.client.put_slots.assert_not_called()

    def test_uncertain_write_consumes_plan_and_requires_inspection(self):
        plan = self.service.preview_fill(DAY)
        self.client.put_slots.side_effect = requests.Timeout("private-url-or-token")
        with self.assertRaisesRegex(HelperError, "may have succeeded"):
            self.service.apply_plan(plan["plan_id"])
        with self.assertRaisesRegex(HelperError, "used plan"):
            self.service.apply_plan(plan["plan_id"])
        self.assertEqual(self.client.put_slots.call_count, 1)

    def test_non_success_response_is_not_reported_as_submitted(self):
        plan = self.service.preview_fill(DAY)
        self.client.put_slots.return_value = 500
        with self.assertRaisesRegex(HelperError, "did not confirm success"):
            self.service.apply_plan(plan["plan_id"])

    def test_read_only_service_rejects_apply(self):
        self.service.allow_writes = False
        plan = self.service.preview_fill(DAY)
        with self.assertRaisesRegex(HelperError, "Writes are disabled"):
            self.service.apply_plan(plan["plan_id"])
        self.client.put_slots.assert_not_called()

    def test_inspection_reads_open_afternoon_even_when_summary_has_out(self):
        self.client.diaries.return_value = [diary(**{"in": "09:00:02", "out": "13:00:02", "isPending": True})]
        self.client.diary_slots.return_value = open_slots()
        result = self.service.inspect_days(DAY, DAY)
        self.assertEqual(result["repairable_days"], 1)
        self.assertEqual(result["days"][0]["state"], "open")
        self.assertTrue(result["days"][0]["pending_approval"])
        self.assertNotIn("private-address", json.dumps(result))

    def test_missing_api_day_is_not_invented_and_invalid_ranges_fail(self):
        self.client.diaries.return_value = []
        self.assertEqual(self.service.inspect_days(DAY, DAY)["days"], [])
        with self.assertRaisesRegex(HelperError, "one diary"):
            self.service.preview_fill(DAY)
        for start, end in (("2026-10-01", "2026-11-01"), (DAY, "2026-10-29"),
                           ("20261030", DAY), (DAY, "2026-11-03")):
            with self.subTest(start=start, end=end), self.assertRaises(HelperError):
                self.service.inspect_days(start, end)

    def test_settings_never_load_credentials_or_include_paths(self):
        self.service.client_factory = Mock(side_effect=AssertionError("credentials loaded"))
        result = self.service.settings()
        self.assertNotIn("env_file", result["schedule"])
        self.assertNotIn("pause_dir", result["schedule"])
        self.assertEqual(result["schedule"]["min_lunch_minutes"], 30)


@unittest.skipUnless(MCP_AVAILABLE, "Install .[mcp] on Python 3.10+ for protocol tests")
class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_handshake_schema_config_and_error_without_credentials(self):
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "woffu_mcp", "--config", str(ROOT / "config.example.json")],
            cwd=str(ROOT), env={"WOFFU_DOMAIN": "", "WOFFU_TOKEN": ""})
        async with Client(params, read_timeout_seconds=10) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual(len(tools), 5)
            self.assertNotIn("woffu_apply_plan", [t.name for t in tools])
            self.assertTrue(all(t.annotations.read_only_hint for t in tools))
            self.assertTrue(all(t.output_schema for t in tools))
            result = await client.call_tool("woffu_get_config", {})
            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content["schedule"]["start"], "09:00")
            error = await client.call_tool("woffu_today", {})
            self.assertTrue(error.is_error)
            self.assertIn("WOFFU_DOMAIN", error.content[0].text)
            self.assertFalse((await client.call_tool("woffu_get_config", {})).is_error)

    async def test_protocol_preview_apply_and_annotations(self):
        with tempfile.TemporaryDirectory() as directory, patch("woffu_mcp.date", Monday):
            options = config.defaults(pause_dir=directory, extra_minutes=0)
            api = Mock(spec=woffu.Woffu)
            api.base, api.user_id = "https://example.woffu.com", 42
            api.diaries.return_value = [diary()]
            api.diary_slots.return_value = []
            api.put_slots.return_value = 204
            server = create_server(options, True, lambda: api)
            async with Client(server, read_timeout_seconds=10) as client:
                tools = (await client.list_tools()).tools
                apply = next(t for t in tools if t.name == "woffu_apply_plan")
                self.assertFalse(apply.annotations.read_only_hint)
                self.assertTrue(apply.annotations.destructive_hint)
                preview = await client.call_tool("woffu_preview_fill", {"day": DAY})
                self.assertFalse(preview.is_error)
                api.put_slots.assert_not_called()
                arguments = {"plan_id": preview.structured_content["plan_id"]}
                result = await client.call_tool("woffu_apply_plan", arguments)
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["status"], "submitted")
                self.assertTrue((await client.call_tool("woffu_apply_plan", arguments)).is_error)
                self.assertEqual(api.put_slots.call_count, 1)

    async def test_request_errors_are_redacted(self):
        api = Mock(spec=woffu.Woffu)
        api.live_slots.side_effect = requests.ConnectionError("secret-token-private-host")
        async with Client(create_server(config.defaults(), client_factory=lambda: api)) as client:
            result = await client.call_tool("woffu_today", {})
            self.assertTrue(result.is_error)
            self.assertNotIn("secret-token", result.content[0].text)
            self.assertNotIn("private-host", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
