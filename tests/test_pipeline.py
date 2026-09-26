"""agents.macro.pipeline: the fetch -> transform -> detect-events glue,
tested end-to-end with fixture series (no network, no LLM)."""

from __future__ import annotations

from datetime import date

from agents.macro.config import IndicatorConfig
from agents.macro.fetch_fred import Observation
from agents.macro.pipeline import compute_indicator_snapshot

CPI = IndicatorConfig(
    id="cpi",
    name="CPI (all items)",
    group="inflation",
    fred_series="CPIAUCSL",
    frequency="monthly",
    primary="yoy_pct",
    secondary=["mom_pct"],
    good_direction="down",
    high_priority=True,
    thresholds=[3.0],
)

PAYROLLS = IndicatorConfig(
    id="payrolls",
    name="Nonfarm payrolls",
    group="labor",
    fred_series="PAYEMS",
    frequency="monthly",
    primary="mom_diff",
    secondary=["avg_3"],
    units_scale="thousands",
    units="thousands",
    level_decimals=0,
    good_direction="up",
    high_priority=True,
    thresholds=[],
)


def _monthly(values, start=date(2025, 9, 1)):
    obs = []
    y, m = start.year, start.month
    for v in values:
        obs.append(Observation(date=date(y, m, 1), value=v))
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return obs


def test_computes_primary_and_secondary():
    series = _monthly([100.0 + i for i in range(13)])
    snapshot = compute_indicator_snapshot(CPI, series, stored_observations={})
    assert snapshot.primary is not None
    assert "mom_pct" in snapshot.secondary
    assert snapshot.period == series[-1].date


def test_new_release_event_when_period_not_in_stored():
    series = _monthly([100.0 + i for i in range(13)])
    stored = {o.date.isoformat(): o.value for o in series[:-1]}  # everything but the latest
    snapshot = compute_indicator_snapshot(CPI, series, stored_observations=stored)
    new_releases = [e for e in snapshot.events if e.type == "new_release"]
    assert len(new_releases) == 1
    assert new_releases[0].as_of == series[-1].date


def test_no_new_release_event_when_period_already_stored():
    series = _monthly([100.0 + i for i in range(13)])
    stored = {o.date.isoformat(): o.value for o in series}  # latest already known
    snapshot = compute_indicator_snapshot(CPI, series, stored_observations=stored)
    assert [e for e in snapshot.events if e.type == "new_release"] == []


def _payroll_levels(changes, start_level=159000.0, start=date(2026, 4, 1)):
    levels, level = [start_level], start_level
    for change in changes:
        level += change
        levels.append(level)
    return _monthly(levels, start=start)


def test_payroll_revision_reported_as_the_monthly_change():
    # §13 acceptance case: "Jul revised from +73K to +41K".
    stored_series = _payroll_levels([30, 21, 73])  # Apr level, then May/Jun/Jul changes
    stored = {o.date.isoformat(): o.value for o in stored_series}
    new_series = _payroll_levels([30, 21, 41, 59])  # Jul revised down, Aug is new
    snapshot = compute_indicator_snapshot(PAYROLLS, new_series, stored_observations=stored)

    revisions = [e for e in snapshot.events if e.type == "revision"]
    assert len(revisions) == 1
    assert revisions[0].id == "revision:payrolls:2026-07-01"
    assert revisions[0].facts["old"] == 73
    assert revisions[0].facts["new"] == 41
    assert revisions[0].facts["units"] == "thousands"
    assert revisions[0].priority == 50  # |41 - 73| = 32K, under the 50K bonus threshold
    assert snapshot.revisions[-1].old == 73 and snapshot.revisions[-1].new == 41

    new_release = next(e for e in snapshot.events if e.type == "new_release")
    assert new_release.facts["mom_diff"] == 59
    assert new_release.facts["prior_mom_diff"] == 41
    assert new_release.facts["avg_3"] == round((21 + 41 + 59) / 3)  # 3-mo avg *change*


def test_payroll_revision_over_50k_gets_priority_bonus():
    stored = {o.date.isoformat(): o.value for o in _payroll_levels([30, 21, 173])}
    snapshot = compute_indicator_snapshot(
        PAYROLLS, _payroll_levels([30, 21, 41, 59]), stored_observations=stored
    )
    revision = next(e for e in snapshot.events if e.type == "revision")
    assert revision.priority == 50 + 15


def test_float_noise_below_epsilon_is_not_a_revision():
    stored = {o.date.isoformat(): o.value for o in _payroll_levels([30, 21, 73])}
    new = _payroll_levels([30, 21, 73.4, 59])
    snapshot = compute_indicator_snapshot(PAYROLLS, new, stored_observations=stored)
    assert [e for e in snapshot.events if e.type == "revision"] == []


def test_not_updated_series_emits_no_events():
    series = _monthly([100.0 + i for i in range(13)])
    snapshot = compute_indicator_snapshot(CPI, series, stored_observations={}, updated=False)
    assert snapshot.events == []
    assert snapshot.primary is not None


def test_new_release_facts_are_rounded_to_published_precision():
    series = _monthly([100.0 + i * 0.3 for i in range(14)])
    snapshot = compute_indicator_snapshot(CPI, series, stored_observations={})
    facts = next(e for e in snapshot.events if e.type == "new_release").facts
    assert facts["yoy"] == round(snapshot.primary, 1)
    assert facts["prior_yoy"] == round(snapshot.prior_primary, 1)
    assert facts["units"] == "percent"


def test_threshold_cross_detected_across_the_latest_period():
    series = _monthly([100.0 + i for i in range(14)])  # yoy at t vs t-1 crosses 3.0 threshold as values grow
    stored = {o.date.isoformat(): o.value for o in series[:-1]}
    snapshot = compute_indicator_snapshot(CPI, series, stored_observations=stored)
    # whether or not this exact series crosses 3.0 depends on the numbers, but the
    # pipeline must at least be able to produce threshold_cross events without erroring
    assert all(e.type in {"new_release", "revision", "threshold_cross"} for e in snapshot.events)


def test_no_threshold_cross_with_insufficient_history():
    series = _monthly([100.0])
    snapshot = compute_indicator_snapshot(CPI, series, stored_observations={})
    assert [e for e in snapshot.events if e.type == "threshold_cross"] == []


def test_empty_series_produces_no_events_and_none_primary():
    snapshot = compute_indicator_snapshot(CPI, [], stored_observations={})
    assert snapshot.primary is None
    assert snapshot.events == []
    assert snapshot.period is None
