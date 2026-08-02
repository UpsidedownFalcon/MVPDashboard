"""Duration string parsing/formatting — syntax `<int><s|m|h|d|w>` (TRD §7)."""

from __future__ import annotations

import re
from datetime import timedelta

_UNIT_TO_KWARG = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_UNIT_SECONDS = {"w": 604_800, "d": 86_400, "h": 3_600, "m": 60, "s": 1}
_DURATION_RE = re.compile(r"^(\d+)([smhdw])$")


def parse_duration(text: str) -> timedelta:
    """Parse '5m' / '2h' / '30d' … into a timedelta.

    Raises ValueError on anything that is not `<int><s|m|h|d|w>`.
    """
    if not isinstance(text, str):
        raise ValueError(f"duration must be a string, got {type(text).__name__}")
    match = _DURATION_RE.match(text.strip())
    if match is None:
        raise ValueError(f"invalid duration {text!r}: expected <int><s|m|h|d|w>, e.g. '5m'")
    value, unit = int(match.group(1)), match.group(2)
    return timedelta(**{_UNIT_TO_KWARG[unit]: value})


def parse_duration_list(text: str) -> list[timedelta]:
    """Parse a comma-separated duration list, e.g. '5m,30m,2h'."""
    if not isinstance(text, str):
        raise ValueError(f"duration list must be a string, got {type(text).__name__}")
    items = [item for item in (part.strip() for part in text.split(",")) if item]
    if not items:
        raise ValueError(f"invalid duration list {text!r}: no durations found")
    return [parse_duration(item) for item in items]


def format_duration(td: timedelta) -> str:
    """Inverse of parse_duration, for UI labels: the largest unit that divides evenly.

    timedelta(minutes=90) -> '90m', timedelta(hours=2) -> '2h'.
    """
    total = td.total_seconds()
    seconds = int(total)
    if seconds != total or seconds <= 0:
        raise ValueError(f"cannot format {td!r}: need a positive whole number of seconds")
    for unit, unit_seconds in _UNIT_SECONDS.items():
        if seconds % unit_seconds == 0:
            return f"{seconds // unit_seconds}{unit}"
    raise AssertionError("unreachable: seconds always divides by 1")
