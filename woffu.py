"""Minimal Woffu client, built from the endpoints the Woffu web app uses.

Config comes from the environment (or ~/.config/woffu/env):
  WOFFU_DOMAIN  your company's Woffu host, e.g. acme.woffu.com
  WOFFU_TOKEN   the JWT from the web app's `authorization` header
"""

import base64
import json
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

ENV_FILE = Path.home() / ".config" / "woffu" / "env"

# Sign enums used by the web app.
UNCHANGED, CHANGED = 0, 1
TYPE_UPDATED = 3


# --- time helpers -----------------------------------------------------------

def to_min(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def to_time(total):
    return f"{total // 60:02d}:{total % 60:02d}:00"


def jitter(hhmm, variance):
    return to_time(to_min(hhmm) + random.randint(-variance, variance))


# --- diary predicates -------------------------------------------------------

def _has_real_in(diary):
    in_val = str(diary.get("in") or "")
    return ":" in in_val and in_val != "_FlexibleSchedule"


def has_absence(diary):
    return bool(diary.get("absenceEvents") or diary.get("pendingAbsenceEvents"))


def needs_filling(diary):
    """Workday with nothing recorded."""
    return not (diary["isWeekend"] or diary["isHoliday"] or diary.get("accepted")
                or has_absence(diary) or _has_real_in(diary))


def is_open(diary):
    """Past workday that was clocked in but never clocked out."""
    return (not diary["isWeekend"] and _has_real_in(diary)
            and not diary.get("out") and not diary.get("isToday"))


# --- sign / slot payloads ---------------------------------------------------

def new_sign(hhmmss, sign_in):
    return {
        "new": True, "deleted": False, "agreementEventId": None, "code": None,
        "iP": None, "requestId": None, "signId": 0, "signStatus": CHANGED,
        "signType": TYPE_UPDATED, "time": hhmmss, "deviceId": None,
        "signIn": sign_in, "userId": 0,
    }


def existing_sign(api_sign):
    """Echo a sign from GET .../workday/slots back unchanged."""
    return {
        "new": False, "deleted": False,
        "agreementEventId": api_sign["AgreementEventId"],
        "code": api_sign["Code"], "iP": api_sign["IP"],
        "requestId": api_sign["RequestId"], "signId": api_sign["SignId"],
        "signStatus": UNCHANGED, "signType": api_sign["SignType"],
        "time": api_sign["Time"], "deviceId": api_sign["DeviceId"],
        "signIn": api_sign["SignIn"], "userId": api_sign["UserId"],
    }


def make_slot(in_sign, out_sign, order):
    total = to_min(out_sign["time"]) - to_min(in_sign["time"]) if out_sign else 0
    return {"id": str(int(time.time() * 1000) + order), "motive": None,
            "in": in_sign, "out": out_sign, "order": order, "totalMin": total}


# --- client -----------------------------------------------------------------

def _load_env_file():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class Woffu:
    def __init__(self):
        _load_env_file()
        domain = os.environ.get("WOFFU_DOMAIN")
        token = os.environ.get("WOFFU_TOKEN", "").removeprefix("Bearer ").strip()
        if not domain or not token:
            raise SystemExit("Set WOFFU_DOMAIN and WOFFU_TOKEN (see README).")
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if claims.get("exp", 0) < time.time():
            raise SystemExit("WOFFU_TOKEN has expired; copy a fresh one.")
        self.user_id = int(claims["UserId"])
        self.base = f"https://{domain}"
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "origin": self.base,
            "referer": f"{self.base}/v2/personal/diary/user",
        }

    def _get(self, path, **params):
        resp = requests.get(self.base + path, headers=self.headers, params=params)
        resp.raise_for_status()
        return resp.json()

    # Diary (past days) ------------------------------------------------------

    def diaries(self, from_date, to_date):
        """Diary summaries, fetched in 31-day chunks (the API caps a page at 100)."""
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")
        out = []
        while start <= end:
            chunk_end = min(start + timedelta(days=30), end)
            out += self._get(
                f"/api/svc/core/diariesquery/users/{self.user_id}"
                "/diaries/summary/presence",
                userId=self.user_id, fromDate=f"{start:%Y-%m-%d}",
                toDate=f"{chunk_end:%Y-%m-%d}", pageSize=100,
                includeHourTypes="true", includeNotHourTypes="true",
                includeDifference="true",
            )["diaries"]
            start = chunk_end + timedelta(days=1)
        return out

    def diary_slots(self, diary_id):
        return self._get(f"/api/diaries/{diary_id}/workday/slots")["Slots"]

    def put_slots(self, diary_id, date, slots):
        """Replace a day's slots. Edits to past days may need approval."""
        body = {"userId": self.user_id, "comments": "", "date": date,
                "slots": slots, "diaryId": diary_id}
        resp = requests.put(f"{self.base}/api/diaries/{diary_id}/workday/slots/self",
                            headers=self.headers, json=body)
        return resp.status_code

    def plan_close(self, diary_id, hours, variance):
        """Slots that keep every existing sign and clock out the open one so
        the day totals ~`hours`. None if the day isn't simply open."""
        api_slots = self.diary_slots(diary_id)
        if not api_slots or api_slots[-1]["Out"] is not None:
            return None
        slots, worked = [], 0
        for i, s in enumerate(api_slots, 1):
            in_sign = existing_sign(s["In"])
            out_sign = existing_sign(s["Out"]) if s["Out"] else None
            if out_sign:
                worked += to_min(out_sign["time"]) - to_min(in_sign["time"])
            else:
                remaining = max(hours * 60 - worked, 30)
                out_time = jitter(to_time(to_min(in_sign["time"]) + remaining), variance)
                out_sign = new_sign(out_time, False)
            slots.append(make_slot(in_sign, out_sign, i))
        return slots

    # Live signing (today) ---------------------------------------------------

    def live_slots(self):
        return self._get("/api/svc/signs/v2/signs/slots")

    def sign(self):
        """Toggle clock in/out right now, like the web app's sign button."""
        offset = int(-datetime.now().astimezone().utcoffset().total_seconds() // 60)
        body = {"agreementEventId": None, "requestId": None,
                "latitude": None, "longitude": None, "timezoneOffset": offset}
        resp = requests.post(f"{self.base}/api/svc/signs/v2/signs/signrequest",
                             headers=self.headers, json=body)
        resp.raise_for_status()
