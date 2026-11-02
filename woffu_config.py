"""Schedule configuration. CLI options override JSON, then built-in defaults."""

import argparse
import json
import math
import os
from datetime import datetime, time
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "woffu"
DEFAULT_CONFIG = CONFIG_DIR / "config.json"
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Preserve the original no-config behavior for existing installations.
DEFAULTS = {
    "start": "07:00", "lunch": "13:00-13:30", "end": "16:30",
    "work_hours": 8, "extra_minutes": 10, "extra_variance": 5,
    "min_lunch_minutes": 30, "lunch_variance": 6,
    "end_early_minutes": 5, "end_late_minutes": 10,
    "weekdays": "mon,tue,wed,thu,fri", "recovery": "both", "recovery_days": 7,
    "pause_dir": str(CONFIG_DIR), "env_file": None, "request_timeout": 20,
}
SCHEDULE_FIELDS = {
    "start", "lunch", "end", "work_hours", "extra_minutes", "extra_variance",
    "min_lunch_minutes", "lunch_variance", "end_early_minutes", "end_late_minutes",
}


def clock(value):
    if isinstance(value, time):
        return value
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        raise ValueError("times must use HH:MM (00:00 to 23:59)") from None


def _minutes(value):
    return value.hour * 60 + value.minute


def validate(values):
    result = dict(values)
    for name in ("start", "end"):
        result[name] = clock(result[name])
    for name in ("extra_minutes", "extra_variance", "min_lunch_minutes", "lunch_variance",
                 "end_early_minutes", "end_late_minutes", "recovery_days"):
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    if not 1 <= result["recovery_days"] <= 366:
        raise ValueError("recovery_days must be between 1 and 366; use recovery='off' to disable")
    for name in ("work_hours", "request_timeout"):
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number")
    if not 0 < round(result["work_hours"] * 60) < 24 * 60:
        raise ValueError("work_hours must be at least one minute and less than 24 hours")
    if result["recovery"] not in ("off", "close", "fill", "both"):
        raise ValueError("recovery must be off, close, fill, or both")
    weekdays = result["weekdays"]
    if isinstance(weekdays, str):
        weekdays = weekdays.split(",")
    if not isinstance(weekdays, (list, tuple)) or not weekdays:
        raise ValueError("weekdays must be a nonempty list, e.g. ['mon', 'tue', 'wed', 'thu', 'fri']")
    if any(not isinstance(day, str) or day.strip().lower() not in DAY_NAMES for day in weekdays):
        raise ValueError("weekdays must use mon,tue,wed,thu,fri,sat,sun")
    result["weekdays"] = tuple(DAY_NAMES.index(day.strip().lower()) for day in weekdays)
    for name in ("pause_dir", "env_file"):
        value = result[name]
        if value is None and name == "env_file":
            continue
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError(f"{name} must be a file path")
        result[name] = Path(value).expanduser()
    start, end = _minutes(result["start"]), _minutes(result["end"])
    if start >= end:
        raise ValueError("start must be before end; overnight shifts are not supported")
    if end - result["end_early_minutes"] <= start or end + result["end_late_minutes"] >= 1440:
        raise ValueError("end variance must stay after start and before midnight")
    lunch = result["lunch"]
    break_minutes = 0
    if lunch != "none":
        if not isinstance(lunch, str) or len(lunch.split("-")) != 2:
            raise ValueError("lunch must be HH:MM-HH:MM or 'none'")
        a, b = map(clock, lunch.split("-"))
        if a >= b:
            raise ValueError("lunch must end after it starts, within the same day")
        latest_start = _minutes(a) + result["lunch_variance"]
        latest_end = max(_minutes(b) + result["lunch_variance"],
                         latest_start + result["min_lunch_minutes"])
        if latest_end >= 1440:
            raise ValueError("lunch and its minimum duration must fit before midnight")
        if start < _minutes(a):
            if latest_end >= end - result["end_early_minutes"]:
                raise ValueError("end must allow returning from lunch, including variance")
            break_minutes = max(_minutes(b) - _minutes(a) + result["lunch_variance"],
                                result["min_lunch_minutes"])
        result["lunch"] = f"{a:%H:%M}-{b:%H:%M}"
    maximum_extra = result["extra_minutes"] + result["extra_variance"] if result["extra_minutes"] else 0
    if start + round(result["work_hours"] * 60) + maximum_extra + break_minutes >= 1440:
        raise ValueError("the work target, extra time, and lunch must fit before midnight")
    return result


def defaults(**overrides):
    """Validated defaults without reading configuration or credentials."""
    return argparse.Namespace(**validate({**DEFAULTS, **overrides}))


def for_day(options, day):
    values = getattr(options, "_day_overrides", {}).get(day.weekday(), {})
    return argparse.Namespace(**{**vars(options), **values})


def parse_args(argv=None, description=None):
    parser = argparse.ArgumentParser(description=description, argument_default=argparse.SUPPRESS)
    parser.add_argument("--config", help=f"JSON configuration (default {DEFAULT_CONFIG}; WOFFU_CONFIG also supported)")
    parser.add_argument("--start", help="earliest clock-in, HH:MM (default 07:00)")
    parser.add_argument("--lunch", help="HH:MM-HH:MM or none (default 13:00-13:30)")
    parser.add_argument("--end", help="latest time to start/resume work, HH:MM (default 16:30)")
    parser.add_argument("--work-hours", "--close-hours", dest="work_hours", type=float,
                        help="daily worked hours, excluding breaks (default 8)")
    parser.add_argument("--extra-minutes", type=int, help="extra time per day; 0 disables (default 10)")
    parser.add_argument("--extra-variance", type=int, help="± minutes around extra time (default 5)")
    parser.add_argument("--min-lunch-minutes", type=int, help="minimum actual lunch duration (default 30)")
    parser.add_argument("--lunch-variance", type=int, help="0..N minute delay on lunch boundaries (default 6)")
    parser.add_argument("--end-early-minutes", type=int, help="maximum earlier end cutoff (default 5)")
    parser.add_argument("--end-late-minutes", type=int, help="maximum later end cutoff (default 10)")
    parser.add_argument("--weekdays", help="comma-separated working days (default mon,tue,wed,thu,fri)")
    parser.add_argument("--recovery", choices=("off", "close", "fill", "both"),
                        help="automatic repairs: off, close, fill, both (default both)")
    parser.add_argument("--recovery-days", type=int, help="past calendar days to inspect (default 7)")
    parser.add_argument("--pause-dir", help=f"directory for pause files (default {CONFIG_DIR})")
    parser.add_argument("--env-file", help="credential file; environment variables take precedence")
    parser.add_argument("--request-timeout", type=float, help="HTTP timeout in seconds (default 20)")
    parser.add_argument("--dry-run", action="store_true", help="preview actions without writing to Woffu")
    parser.add_argument("--show-config", action="store_true", help="print settings without accessing Woffu")
    supplied = vars(parser.parse_args(argv))
    explicit = supplied.pop("config", None) or os.environ.get("WOFFU_CONFIG")
    path = Path(explicit).expanduser() if explicit else DEFAULT_CONFIG
    controls = {name: supplied.pop(name, False) for name in ("dry_run", "show_config")}
    try:
        if explicit or path.exists():
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("configuration must be a JSON object")
        else:
            data = {}
        unknown = set(data) - set(DEFAULTS) - {"weekday_overrides"}
        if unknown:
            raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
        for name in ("pause_dir", "env_file"):
            if data.get(name) and isinstance(data[name], str):
                value = Path(data[name]).expanduser()
                data[name] = value if value.is_absolute() else path.parent / value
        overrides = data.pop("weekday_overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError("weekday_overrides must be an object keyed by day name")
        values = {**DEFAULTS, **data, **supplied}
        settings = validate(values)
        resolved = {}
        for name, changes in overrides.items():
            if name not in DAY_NAMES or not isinstance(changes, dict) or set(changes) - SCHEDULE_FIELDS:
                raise ValueError("weekday_overrides must use mon..sun keys and schedule settings only")
            resolved[DAY_NAMES.index(name)] = validate({**DEFAULTS, **data, **changes, **supplied})
    except (OSError, ValueError, TypeError) as exc:
        parser.error(f"configuration: {exc}")
    return argparse.Namespace(**settings, **controls, config=path,
                              weekday_overrides=overrides, _day_overrides=resolved)


def as_dict(options):
    result = {name: getattr(options, name) for name in DEFAULTS}
    for name, value in result.items():
        if isinstance(value, time):
            result[name] = value.strftime("%H:%M")
        elif isinstance(value, Path):
            result[name] = str(value)
    result["weekdays"] = [DAY_NAMES[i] for i in options.weekdays]
    result["weekday_overrides"] = {}
    for day, values in getattr(options, "_day_overrides", {}).items():
        result["weekday_overrides"][DAY_NAMES[day]] = {
            name: values[name].strftime("%H:%M") if isinstance(values[name], time) else values[name]
            for name in sorted(SCHEDULE_FIELDS) if values[name] != getattr(options, name)
        }
    return result
