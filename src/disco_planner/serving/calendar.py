"""Retail calendar and time-of-day features.

Pure functions on a (local) datetime plus config/calendar.yaml. Only needs the standard
library and pyyaml, so campaign planning can reuse it (flight dates, budget front-loading).
"""
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import yaml

CALENDAR_PATH = Path(__file__).resolve().parents[3] / "config" / "calendar.yaml"
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass(frozen=True)
class Moment:
    """A retail moment that is active (or approaching) on a given day."""
    name: str
    intensity: float            # 0..1: 1 inside the window, ramping up during lead_days
    days_until_start: int       # 0 once the window has started
    gifting: bool
    tags: tuple[str, ...]
    boosts_personas: tuple[str, ...]


@lru_cache(maxsize=1)
def load_events(path: Path = CALENDAR_PATH) -> list[dict]:
    """Read the event list from YAML (cached)."""
    with open(path) as handle:
        return yaml.safe_load(handle)["events"]


def _as_date(value) -> date:
    """YAML gives date objects for 2026-11-27; accept plain strings too."""
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _intensity(today: date, start: date, end: date, lead_days: int) -> tuple[float, int]:
    """Return (intensity, days_until_start) for one window; intensity 0 means inactive."""
    if start <= today <= end:
        return 1.0, 0
    days_before = (start - today).days
    if 0 < days_before <= lead_days:
        return 1 - days_before / (lead_days + 1), days_before
    return 0.0, days_before


def active_moments(local_dt: datetime, events: list[dict] | None = None) -> list[Moment]:
    """Moments with intensity > 0 on this local date, strongest first."""
    today = local_dt.date()
    found = []
    for event in events if events is not None else load_events():
        best = (0.0, 0)
        for start, end in event["windows"]:
            candidate = _intensity(today, _as_date(start), _as_date(end), event["lead_days"])
            if candidate[0] > best[0]:
                best = candidate
        if best[0] > 0:
            found.append(Moment(
                name=event["name"], intensity=round(best[0], 3), days_until_start=best[1],
                gifting=bool(event.get("gifting")), tags=tuple(event.get("tags", [])),
                boosts_personas=tuple(event.get("boosts_personas", []))))
    return sorted(found, key=lambda m: m.intensity, reverse=True)


def daypart(hour: int) -> str:
    """morning 5-11, afternoon 12-16, evening 17-21, late_night 22-4."""
    if 5 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 16:
        return "afternoon"
    if 17 <= hour <= 21:
        return "evening"
    return "late_night"


def time_features(local_dt: datetime) -> dict:
    """Day of week, weekend flag, hour and daypart of a local datetime."""
    return {"day_of_week": DAY_NAMES[local_dt.weekday()],
            "is_weekend": local_dt.weekday() >= 5,
            "local_hour": local_dt.hour,
            "daypart": daypart(local_dt.hour)}
