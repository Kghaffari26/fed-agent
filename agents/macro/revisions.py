"""Revision detection (§5.3): compare stored state to newly fetched observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from agents.macro.fetch_fred import Observation

PERCENT_EPSILON = 0.05
COUNT_EPSILON = 1.0  # for thousands-level counts, e.g. payrolls
MAX_SURFACED_PERIODS = 3


@dataclass
class Revision:
    period: date
    old: float
    new: float


def epsilon_for(units_scale: str | None) -> float:
    """§5.3: 0.05 for percents, 1 for thousands-level counts (e.g. payrolls)."""
    return COUNT_EPSILON if units_scale == "thousands" else PERCENT_EPSILON


def detect_revisions(
    stored: dict[str, float | None],
    new: list[Observation],
    *,
    units_scale: str | None = None,
) -> list[Revision]:
    """Compare every overlapping date between `stored` (date-ISO -> value) and
    `new`. A revision is `|new - old| > epsilon`. Only the most recent
    `MAX_SURFACED_PERIODS` overlapping periods are surfaced (§5.3).
    """
    eps = epsilon_for(units_scale)
    revisions: list[Revision] = []
    for obs in new:
        if obs.value is None:
            continue
        old_value = stored.get(obs.date.isoformat())
        if old_value is None:
            continue
        if round(abs(obs.value - old_value), 6) > eps:
            revisions.append(Revision(period=obs.date, old=old_value, new=obs.value))
    revisions.sort(key=lambda r: r.period)
    return revisions[-MAX_SURFACED_PERIODS:]
