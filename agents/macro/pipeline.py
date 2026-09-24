"""Per-indicator pipeline glue: fetched series -> computed values -> events.

This is the "transform -> detect events" stage of the §4 pipeline diagram,
independent of both the HTTP transport and the LLM — it's what `core.runner`
(via agents_core, once installable) will call between fetch and analyze.
Fully testable now with fixture series; see STATUS.md for what's still
blocked (the actual agents_core.agent.Agent registration).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from agents.macro.config import IndicatorConfig
from agents.macro.events import Event, new_release_event, revision_event, threshold_cross_events
from agents.macro.fetch_fred import Observation
from agents.macro.revisions import Revision, detect_revisions
from agents.macro.transform import apply_transform


@dataclass
class IndicatorSnapshot:
    indicator_id: str
    primary: float | None
    secondary: dict[str, float | None] = field(default_factory=dict)
    period: date | None = None
    revisions: list[Revision] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


def compute_indicator_snapshot(
    indicator: IndicatorConfig,
    series: list[Observation],
    stored_observations: dict[str, float | None],
) -> IndicatorSnapshot:
    """`series` is the freshly fetched observations (already merged with
    history as needed by the caller); `stored_observations` is last run's
    state for this series (date-iso -> value), used for revision and
    new-release detection."""
    primary = apply_transform(indicator.primary, series, indicator.frequency)
    secondary = {name: apply_transform(name, series, indicator.frequency) for name in indicator.secondary}
    period = series[-1].date if series else None

    revisions = detect_revisions(stored_observations, series, units_scale=indicator.units_scale)

    events: list[Event] = []
    latest = series[-1] if series else None
    is_new = (
        latest is not None
        and latest.value is not None
        and latest.date.isoformat() not in stored_observations
    )
    if is_new:
        events.append(
            new_release_event(
                indicator.id,
                period=latest.date,
                high_priority=indicator.high_priority,
                facts={"value": latest.value, **secondary},
            )
        )

    uses_mom_diff = "mom_diff" in {indicator.primary, *indicator.secondary}
    is_payrolls = indicator.units_scale == "thousands" and uses_mom_diff
    for revision in revisions:
        events.append(revision_event(indicator.id, revision, is_payrolls=is_payrolls))

    if indicator.thresholds and len(series) >= 2 and primary is not None:
        prior_primary = apply_transform(indicator.primary, series[:-1], indicator.frequency)
        events.extend(
            threshold_cross_events(
                indicator.id,
                prior_value=prior_primary,
                new_value=primary,
                thresholds=indicator.thresholds,
                period=period or date.today(),
            )
        )

    return IndicatorSnapshot(
        indicator_id=indicator.id,
        primary=primary,
        secondary=secondary,
        period=period,
        revisions=revisions,
        events=events,
    )
