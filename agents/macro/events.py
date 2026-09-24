"""Delayed-data detection (§5.5) and event detection + priority ranking (§5.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from agents.macro.revisions import Revision

DELAYED_GRACE_DAYS = 2
CURVE_SIGN_CONFIRMATION_DAYS = 5

EventType = str  # fomc_decision | new_release | revision | threshold_cross |
# curve_sign_change | extreme | regime_change | minutes_released | delayed

BASE_PRIORITY: dict[EventType, int] = {
    "fomc_decision": 100,
    "new_release": 60,
    "revision": 50,
    "threshold_cross": 70,
    "curve_sign_change": 75,
    "extreme": 55,
    "regime_change": 85,
    "minutes_released": 65,
    "delayed": 40,
}
HIGH_PRIORITY_NEW_RELEASE = 80
PAYROLL_REVISION_BONUS_THRESHOLD = 50.0  # thousands
PAYROLL_REVISION_BONUS = 15


@dataclass
class Event:
    id: str
    type: EventType
    priority: int
    facts: dict = field(default_factory=dict)
    as_of: date | None = None


def is_delayed(*, scheduled_release: date, today: date, has_new_observation: bool) -> bool:
    """§5.5: a release is `delayed` once >2 days late with nothing posted yet."""
    if has_new_observation:
        return False
    return (today - scheduled_release).days > DELAYED_GRACE_DAYS


def delayed_event(indicator_id: str, *, scheduled_release: date) -> Event:
    return Event(
        id=f"delayed:{indicator_id}:{scheduled_release.isoformat()}",
        type="delayed",
        priority=BASE_PRIORITY["delayed"],
        facts={"scheduled_release": scheduled_release.isoformat()},
    )


def new_release_event(
    indicator_id: str, *, period: date, high_priority: bool, facts: dict
) -> Event:
    priority = HIGH_PRIORITY_NEW_RELEASE if high_priority else BASE_PRIORITY["new_release"]
    return Event(
        id=f"new_release:{indicator_id}:{period.isoformat()}",
        type="new_release",
        priority=priority,
        facts=facts,
        as_of=period,
    )


def revision_event(indicator_id: str, revision: Revision, *, is_payrolls: bool = False) -> Event:
    priority = BASE_PRIORITY["revision"]
    if is_payrolls and abs(revision.new - revision.old) > PAYROLL_REVISION_BONUS_THRESHOLD:
        priority += PAYROLL_REVISION_BONUS
    return Event(
        id=f"revision:{indicator_id}:{revision.period.isoformat()}",
        type="revision",
        priority=priority,
        facts={"period": revision.period.isoformat(), "old": revision.old, "new": revision.new},
        as_of=revision.period,
    )


def threshold_cross_events(
    indicator_id: str, *, prior_value: float | None, new_value: float, thresholds: list[float], period: date
) -> list[Event]:
    """A crossing event per configured threshold the value moved past, either
    direction (§5.6)."""
    events: list[Event] = []
    if prior_value is None:
        return events
    for threshold in thresholds:
        crossed_up = prior_value < threshold <= new_value
        crossed_down = prior_value > threshold >= new_value
        if crossed_up or crossed_down:
            events.append(
                Event(
                    id=f"threshold_cross:{indicator_id}:{threshold}:{period.isoformat()}",
                    type="threshold_cross",
                    priority=BASE_PRIORITY["threshold_cross"],
                    facts={
                        "threshold": threshold,
                        "prior": prior_value,
                        "new": new_value,
                        "direction": "up" if crossed_up else "down",
                    },
                    as_of=period,
                )
            )
    return events


def curve_sign_change_event(
    curve_id: str, *, sign_change_date: date, confirmed_through: date
) -> Event | None:
    """5-day confirmation: only surface once the new sign has held for
    `CURVE_SIGN_CONFIRMATION_DAYS` days."""
    if (confirmed_through - sign_change_date).days < CURVE_SIGN_CONFIRMATION_DAYS:
        return None
    return Event(
        id=f"curve_sign_change:{curve_id}:{sign_change_date.isoformat()}",
        type="curve_sign_change",
        priority=BASE_PRIORITY["curve_sign_change"],
        facts={"sign_change_date": sign_change_date.isoformat()},
        as_of=sign_change_date,
    )


def extreme_event(indicator_id: str, *, period: date, value: float, kind: str) -> Event:
    """`kind` is "high" or "low"; a new 12-month extreme in a level series."""
    return Event(
        id=f"extreme:{indicator_id}:{kind}:{period.isoformat()}",
        type="extreme",
        priority=BASE_PRIORITY["extreme"],
        facts={"value": value, "kind": kind},
        as_of=period,
    )


def is_extreme(values_last_12m: list[float], latest_value: float) -> str | None:
    """Returns "high", "low", or None. `values_last_12m` excludes `latest_value`."""
    if not values_last_12m:
        return None
    if latest_value > max(values_last_12m):
        return "high"
    if latest_value < min(values_last_12m):
        return "low"
    return None


def regime_change_event(regime_name: str, *, old_label: str, new_label: str, period: date) -> Event | None:
    if old_label == new_label:
        return None
    return Event(
        id=f"regime_change:{regime_name}:{period.isoformat()}",
        type="regime_change",
        priority=BASE_PRIORITY["regime_change"],
        facts={"regime": regime_name, "old": old_label, "new": new_label},
        as_of=period,
    )


def fomc_decision_event(*, statement_date: date, decision: str, target_range: dict) -> Event:
    return Event(
        id=f"fomc_decision:{statement_date.isoformat()}",
        type="fomc_decision",
        priority=BASE_PRIORITY["fomc_decision"],
        facts={"decision": decision, "target_range": target_range},
        as_of=statement_date,
    )


def minutes_released_event(*, meeting_date: date, released_at: date) -> Event:
    return Event(
        id=f"minutes_released:{meeting_date.isoformat()}",
        type="minutes_released",
        priority=BASE_PRIORITY["minutes_released"],
        facts={"meeting_date": meeting_date.isoformat()},
        as_of=released_at,
    )


def rank_events(events: list[Event]) -> list[Event]:
    """Sort by priority (desc), then recency (desc). `as_of=None` sorts last."""
    epoch = date.min
    return sorted(events, key=lambda e: (e.priority, e.as_of or epoch), reverse=True)


def top_events(events: list[Event], limit: int) -> list[Event]:
    return rank_events(events)[:limit]
