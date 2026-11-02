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
from urllib.parse import urlsplit

import requests

ENV_FILE = Path.home() / ".config" / "woffu" / "env"
MIN_LUNCH_MINUTES = 30
REQUEST_TIMEOUT = 20

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


def is_editable(diary):
    """Past workday with no leave, approval, lock, or pending edit."""
    return not (diary.get("isToday") or diary["isWeekend"] or diary["isHoliday"]
                or has_absence(diary) or diary.get("accepted")
                or diary.get("isUserEditable") is False
                or diary.get("hasPendingDiaryChangeUser")
                or diary.get("hasPendingDiaryChangeTargetUser")
                or diary.get("diaryChangeRequestId"))


def needs_filling(diary):
    """Workday with nothing recorded."""
    return is_editable(diary) and not _has_real_in(diary)


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
    """Keep an existing sign's identity, timestamps and metadata."""
    return {**api_sign, "new": False, "signStatus": UNCHANGED}


def make_slot(in_sign, out_sign, order):
    total = to_min(out_sign["time"]) - to_min(in_sign["time"]) if out_sign else 0
    return {"id": str(int(time.time() * 1000) + order), "motive": None,
            "in": in_sign, "out": out_sign, "order": order, "totalMin": total}


def plan_close_slots(api_slots, hours, variance, rnd=None):
    """Close only the final open slot, preserving all recorded signs."""
    if (not api_slots or api_slots[-1].get("out")
            or any(not s.get("in") for s in api_slots)
            or any(not s.get("out") for s in api_slots[:-1])):
        return None
    rnd = rnd or random
    slots, worked = [], timedelta()
    for i, s in enumerate(api_slots, 1):
        in_sign = existing_sign(s["in"])
        start = datetime.fromisoformat(f"2000-01-01T{in_sign['time']}")
        out_sign = existing_sign(s["out"]) if s.get("out") else None
        if out_sign:
            worked += datetime.fromisoformat(f"2000-01-01T{out_sign['time']}") - start
        else:
            remaining = max(timedelta(minutes=round(hours * 60)) - worked, timedelta(minutes=30))
            finish = start + remaining + timedelta(minutes=rnd.randint(-variance, variance))
            if finish <= start or finish.date() != start.date():
                return None
            out_sign = new_sign(finish.strftime("%H:%M:%S"), False)
        slot = make_slot(in_sign, out_sign, i)
        slot["motive"] = s.get("motive")
        slots.append(slot)
    return slots


# --- client -----------------------------------------------------------------

def _load_env_file(env_file=None):
    explicit = env_file or os.environ.get("WOFFU_ENV_FILE")
    path = Path(explicit).expanduser() if explicit else ENV_FILE
    if not explicit and not path.exists():
        return {}
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise SystemExit(f"Cannot read credential file {path}: {exc.strerror}") from None
    values = {}
    for line in lines:
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    return values


class Woffu:
    def __init__(self, env_file=None, request_timeout=REQUEST_TIMEOUT):
        values = _load_env_file(env_file)
        domain = os.environ.get("WOFFU_DOMAIN", values.get("WOFFU_DOMAIN", "")).strip()
        token = os.environ.get("WOFFU_TOKEN", values.get("WOFFU_TOKEN", "")).strip().removeprefix("Bearer ").strip()
        if not domain or not token:
            raise SystemExit("Set WOFFU_DOMAIN and WOFFU_TOKEN (see README).")
        try:
            origin = urlsplit(domain if "://" in domain else f"https://{domain}")
            if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                    or origin.path not in ("", "/") or origin.query or origin.fragment
                    or any(c.isspace() for c in origin.netloc)):
                raise ValueError
            origin.port  # Validate a supplied port before constructing requests.
        except ValueError:
            raise SystemExit("WOFFU_DOMAIN must be a hostname or an HTTPS origin, e.g. acme.woffu.com") from None
        try:
            _, payload, _ = token.split(".")
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            expires = float(claims["exp"])
            self.user_id = int(claims["UserId"])
            if self.user_id <= 0 or not 0 < expires < float("inf"):
                raise ValueError
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise SystemExit("WOFFU_TOKEN is not a valid Woffu JWT; copy the authorization token again.") from None
        if expires < time.time():
            raise SystemExit("WOFFU_TOKEN has expired; copy a fresh one.")
        self.base = f"https://{origin.netloc}"
        self.request_timeout = request_timeout
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "origin": self.base,
            "referer": f"{self.base}/v2/personal/diary/user",
        }

    def _get(self, path, **params):
        resp = requests.get(self.base + path, headers=self.headers, params=params,
                            timeout=self.request_timeout)
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

    def diary_slots(self, diary_summary_id):
        return self._get("/api/svc/core/diariesquery/diarysummaries/"
                         f"{diary_summary_id}/workday/slots/self")["slots"]

    def put_slots(self, day, slots, comments=""):
        """Replace a day's slots. Edits to past days may need approval."""
        # This API accepts local TimeOnly values (HH:mm:ss), not UTC datetimes.
        body = {"userId": self.user_id, "comments": comments, "date": day,
                "slots": slots}
        resp = requests.put(f"{self.base}/api/svc/core/users/{self.user_id}"
                            "/diarysummaries/workday/slots/self",
                            headers=self.headers, json=body, timeout=self.request_timeout)
        resp.raise_for_status()
        return resp.status_code

    def plan_close(self, diary_summary_id, hours, variance):
        """Slots that keep every existing sign and clock out the open one so
        the day totals ~`hours`. None if the day isn't simply open."""
        return plan_close_slots(self.diary_slots(diary_summary_id), hours, variance)

    # Live signing (today) ---------------------------------------------------

    def live_slots(self):
        return self._get("/api/svc/signs/v2/signs/slots")

    def sign(self):
        """Toggle clock in/out right now, like the web app's sign button."""
        offset = int(-datetime.now().astimezone().utcoffset().total_seconds() // 60)
        body = {"agreementEventId": None, "requestId": None,
                "latitude": None, "longitude": None, "timezoneOffset": offset}
        resp = requests.post(f"{self.base}/api/svc/signs/v2/signs/signrequest",
                             headers=self.headers, json=body, timeout=self.request_timeout)
        resp.raise_for_status()
