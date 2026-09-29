"""Helpers for converting between epoch seconds and local dates/times."""

from __future__ import annotations

import time as _time
from datetime import datetime, timedelta
from datetime import time as dtime


def now() -> float:
    return _time.time()


def parse_date(text: str) -> float:
    """Local midnight at the start of a YYYY-MM-DD date."""
    return datetime.strptime(text.strip(), "%Y-%m-%d").timestamp()


def parse_datetime(date_text: str, hm_text: str) -> float:
    return datetime.strptime(f"{date_text.strip()} {hm_text.strip()}", "%Y-%m-%d %H:%M").timestamp()


def fmt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def fmt_duration(seconds: float, with_seconds: bool = False) -> str:
    seconds = max(0, int(round(seconds)))
    if with_seconds:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"
    h, m = divmod((seconds + 30) // 60, 60)
    return f"{h}:{m:02d}"


def fmt_hours(seconds: float) -> str:
    return f"{seconds / 3600:.2f}"


def day_start(ts: float) -> float:
    d = datetime.fromtimestamp(ts).date()
    return datetime.combine(d, dtime.min).timestamp()


def next_day(ts: float) -> float:
    d = datetime.fromtimestamp(ts).date() + timedelta(days=1)
    return datetime.combine(d, dtime.min).timestamp()


def week_start(ts: float) -> float:
    d = datetime.fromtimestamp(ts).date()
    d -= timedelta(days=d.weekday())
    return datetime.combine(d, dtime.min).timestamp()
