"""Per-indicator pipeline glue: fetched series -> computed values -> events (§4-§5.6).

Pure Python: no HTTP, no LLM. `agent.MacroAgent.transform` calls
`compute_indicator_snapshot` once per indicator. Events are only emitted for a series
whose FRED `last_updated` moved since the last run (`updated=True`), which is what
makes an immediate re-run event-free (and so LLM-free).

Event facts are rounded with `display` (the published precision), so the numbers the
LLM receives are exactly the ones the site shows and the number guard checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from agents.macro.config import IndicatorConfig
from agents.macro.display import TRANSFORM_KEYS, display_for, units_display
from agents.macro.events import (
    Event,
    extreme_event,
    is_extreme,
    new_release_event,
    revision_event,
    threshold_cross_events,
)
from agents.macro.fetch_fred import Observation
from agents.macro.revisions import Revision, detect_revisions
from agents.macro.transform import indicator_value

EXTREME_WINDOW_DAYS = 365


@dataclass
class IndicatorSnapshot:
    indicator_id: str
    primary: float | None
    secondary: dict[str, float | None] = field(default_factory=dict)
    period: date | None = None
    prior_primary: float | None = None
    # Revisions in the indicator's *displayed* terms (payrolls: the monthly change, CPI:
    # YoY %), rounded; only those visible at published precision are kept.
    revisions: list[Revision] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


def _value(indicator: IndicatorConfig, name, series: list[Observation]) -> float | None:
    return indicator_value(name, series, indicator.frequency, primary=indicator.primary)


def _latest_index(series: list[Observation]) -> int | None:
    for i in range(len(series) - 1, -1, -1):
        if series[i].value is not None:
            return i
    return None


def revision_epsilon_scale(indicator: IndicatorConfig) -> str | None:
    """§5.3: epsilon 1 for count-like levels, 0.05 for percents/indexes."""
    return "thousands" if indicator.units in ("thousands", "count", "millions") else None


def displayed_revisions(
    indicator: IndicatorConfig, series: list[Observation], stored: dict[str, float | None]
) -> list[Revision]:
    """§5.3 detection on raw values, reported as the primary transform recomputed on
    the old vs the new vintage at each revised period."""
    raw = detect_revisions(stored, series, units_scale=revision_epsilon_scale(indicator))
    if not raw:
        return []
    old_series = [
        Observation(date=o.date, value=stored.get(o.date.isoformat(), o.value))
        if o.date.isoformat() in stored
        else o
        for o in series
    ]
    disp = display_for(indicator, indicator.primary)
    index = {o.date: i for i, o in enumerate(series)}
    out = []
    for rev in raw:
        i = index[rev.period]
        new_value = disp.round(_value(indicator, indicator.primary, series[: i + 1]))
        old_value = disp.round(_value(indicator, indicator.primary, old_series[: i + 1]))
        if new_value is not None and old_value is not None and new_value != old_value:
            out.append(Revision(period=rev.period, old=old_value, new=new_value))
    return out


def new_release_facts(indicator: IndicatorConfig, snapshot: IndicatorSnapshot) -> dict:
    key = TRANSFORM_KEYS[indicator.primary]
    facts: dict = {
        "indicator": indicator.name,
        "period": snapshot.period.isoformat() if snapshot.period else None,
        "units": units_display(indicator),
        key: display_for(indicator, indicator.primary).round(snapshot.primary),
        f"prior_{key}": display_for(indicator, indicator.primary).round(snapshot.prior_primary),
    }
    for name, value in snapshot.secondary.items():
        facts[TRANSFORM_KEYS[name]] = display_for(indicator, name).round(value)
    return facts


def compute_indicator_snapshot(
    indicator: IndicatorConfig,
    series: list[Observation],
    stored_observations: dict[str, float | None],
    *,
    updated: bool = True,
) -> IndicatorSnapshot:
    """`series` is the full merged history (ascending); `stored_observations` is last
    run's state for this series (date-iso -> value)."""
    last = _latest_index(series)
    trimmed = series[: last + 1] if last is not None else []
    primary = _value(indicator, indicator.primary, trimmed) if trimmed else None
    prior_primary = _value(indicator, indicator.primary, trimmed[:-1]) if len(trimmed) > 1 else None
    secondary = {name: _value(indicator, name, trimmed) for name in indicator.secondary}
    period = trimmed[-1].date if trimmed else None
    snapshot = IndicatorSnapshot(
        indicator_id=indicator.id,
        primary=primary,
        secondary=secondary,
        period=period,
        prior_primary=prior_primary,
    )
    if not updated or period is None:
        return snapshot

    snapshot.revisions = displayed_revisions(indicator, trimmed, stored_observations)
    disp = display_for(indicator, indicator.primary)
    events: list[Event] = []

    # Daily series (yields, spreads, policy rates) post every business day; a daily
    # print is not a "release" worth a sentence. They still produce threshold-cross
    # and curve-sign-change events (see DECISIONS.md).
    is_new = period.isoformat() not in stored_observations and primary is not None
    if is_new and indicator.frequency != "daily":
        events.append(
            new_release_event(
                indicator.id,
                period=period,
                high_priority=indicator.high_priority,
                facts=new_release_facts(indicator, snapshot),
            )
        )

    is_payrolls = indicator.primary == "mom_diff" and indicator.units == "thousands"
    for revision in snapshot.revisions:
        event = revision_event(indicator.id, revision, is_payrolls=is_payrolls)
        event.facts = {
            "indicator": indicator.name,
            "units": units_display(indicator),
            **event.facts,
            "revision": disp.round(revision.new - revision.old),
        }
        events.append(event)

    if indicator.thresholds and primary is not None and prior_primary is not None:
        for event in threshold_cross_events(
            indicator.id,
            prior_value=prior_primary,
            new_value=primary,
            thresholds=indicator.thresholds,
            period=period,
        ):
            event.facts = {
                "indicator": indicator.name,
                "units": units_display(indicator),
                "threshold": event.facts["threshold"],
                "prior": disp.round(event.facts["prior"]),
                "new": disp.round(event.facts["new"]),
                "direction": event.facts["direction"],
            }
            events.append(event)

    if indicator.primary == "level" and indicator.frequency != "daily" and is_new:
        window_start = period - timedelta(days=EXTREME_WINDOW_DAYS)
        history = [o.value for o in trimmed[:-1] if o.value is not None and o.date > window_start]
        kind = is_extreme(history, trimmed[-1].value)
        if kind:
            event = extreme_event(indicator.id, period=period, value=disp.round(primary), kind=kind)
            event.facts = {"indicator": indicator.name, "units": units_display(indicator), **event.facts}
            events.append(event)

    snapshot.events = events
    return snapshot
