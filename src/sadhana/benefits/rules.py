"""Insurance frequency rules from the procedure catalog, shared by the product and the simulator."""

from __future__ import annotations

import re
from datetime import date


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def parse_freq(text: str) -> tuple[int, int | None, bool] | None:
    """'2 per 12 months' -> (2, 12, False); '1 per tooth per 60 months' -> (1, 60, True);
    '1 per tooth lifetime' -> (1, None, True). None when the code has no limit."""
    if not text:
        return None
    m = re.match(r"(\d+) per (?:(tooth|quad) )?(?:per )?(?:(\d+) months|lifetime)", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(3)) if m.group(3) else None, bool(m.group(2))


def next_eligible(rule: tuple[int, int | None, bool] | None, covered_dates: list[date], today: date) -> date | None:
    """First date insurance will cover this code again; today if already eligible, None if never again."""
    if rule is None:
        return today
    count, months, _ = rule
    past = sorted(covered_dates)
    if months is None:
        return today if len(past) < count else None
    window_start = add_months(today, -months)
    recent = [d for d in past if d > window_start]
    if len(recent) < count:
        return today
    # the oldest claim in the window has to age out
    oldest = recent[len(recent) - count]
    return max(today, add_months(oldest, months))
