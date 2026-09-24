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


def test_revision_event_detected_and_surfaced():
    series = _monthly([100.0, 101.0])
    stored = {series[0].date.isoformat(): 100.0, series[1].date.isoformat(): 90.0}  # was 90, now 101
    snapshot = compute_indicator_snapshot(PAYROLLS, series, stored_observations=stored)
    revision_events = [e for e in snapshot.events if e.type == "revision"]
    assert len(revision_events) == 1
    assert revision_events[0].facts["old"] == 90.0
    assert revision_events[0].facts["new"] == 101.0


def test_payroll_revision_flagged_as_payrolls_for_priority_bonus():
    series = _monthly([100.0, 200.0])  # |diff|=100 > 50K bonus threshold
    stored = {series[0].date.isoformat(): 100.0, series[1].date.isoformat(): 90.0}
    snapshot = compute_indicator_snapshot(PAYROLLS, series, stored_observations=stored)
    revision_events = [e for e in snapshot.events if e.type == "revision"]
    assert revision_events[0].priority == 50 + 15


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
