"""§5.5 delayed-data detection and §5.6 event detection + priority ranking."""

from __future__ import annotations

from datetime import date

from agents.macro.events import (
    BASE_PRIORITY,
    curve_sign_change_event,
    delayed_event,
    extreme_event,
    fomc_decision_event,
    is_delayed,
    is_extreme,
    minutes_released_event,
    new_release_event,
    rank_events,
    regime_change_event,
    revision_event,
    threshold_cross_events,
    top_events,
)
from agents.macro.revisions import Revision

# -- delayed-data detection (§5.5) ------------------------------------------------


def test_not_delayed_when_observation_arrived():
    result = is_delayed(scheduled_release=date(2026, 9, 1), today=date(2026, 9, 10), has_new_observation=True)
    assert result is False


def test_not_delayed_within_grace_period():
    result = is_delayed(scheduled_release=date(2026, 9, 1), today=date(2026, 9, 3), has_new_observation=False)
    assert result is False


def test_delayed_exactly_at_grace_boundary_is_not_yet_delayed():
    result = is_delayed(scheduled_release=date(2026, 9, 1), today=date(2026, 9, 3), has_new_observation=False)
    assert result is False


def test_delayed_past_grace_period():
    result = is_delayed(scheduled_release=date(2026, 9, 1), today=date(2026, 9, 4), has_new_observation=False)
    assert result is True


# -- priorities -------------------------------------------------------------------


def test_new_release_default_priority():
    e = new_release_event("retail_sales", period=date(2026, 8, 1), high_priority=False, facts={})
    assert e.priority == BASE_PRIORITY["new_release"] == 60


def test_new_release_high_priority_indicator_gets_80():
    e = new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={})
    assert e.priority == 80


def test_revision_base_priority_50():
    r = Revision(period=date(2026, 7, 1), old=73.0, new=71.0)
    e = revision_event("payrolls", r, is_payrolls=True)
    assert e.priority == 50  # |diff|=2, below the 50K bonus threshold


def test_payroll_revision_over_50k_gets_priority_bonus():
    r = Revision(period=date(2026, 7, 1), old=73.0, new=41.0)  # |diff|=32... use bigger
    r_big = Revision(period=date(2026, 7, 1), old=120.0, new=41.0)  # |diff|=79
    e = revision_event("payrolls", r_big, is_payrolls=True)
    assert e.priority == 50 + 15
    e_small = revision_event("payrolls", r, is_payrolls=True)
    assert e_small.priority == 50


def test_non_payroll_revision_never_gets_bonus():
    r = Revision(period=date(2026, 7, 1), old=100.0, new=10.0)  # |diff|=90
    e = revision_event("industrial_production", r, is_payrolls=False)
    assert e.priority == 50


def test_delayed_priority_40():
    e = delayed_event("umich_sentiment", scheduled_release=date(2026, 9, 1))
    assert e.priority == 40


def test_curve_sign_change_priority_75():
    e = curve_sign_change_event(
        "curve_10y2y", sign_change_date=date(2026, 9, 1), confirmed_through=date(2026, 9, 6)
    )
    assert e is not None
    assert e.priority == 75


def test_extreme_priority_55():
    e = extreme_event("treasury_10y", period=date(2026, 9, 1), value=5.1, kind="high")
    assert e.priority == 55


def test_minutes_released_priority_65():
    e = minutes_released_event(meeting_date=date(2026, 7, 29), released_at=date(2026, 8, 19))
    assert e.priority == 65


def test_fomc_decision_priority_100():
    e = fomc_decision_event(
        statement_date=date(2026, 9, 16), decision="hold", target_range={"lower": 4.0, "upper": 4.25}
    )
    assert e.priority == 100


# -- threshold crossings, both directions ------------------------------------------


def test_threshold_cross_upward():
    events = threshold_cross_events(
        "cpi", prior_value=2.8, new_value=3.1, thresholds=[3.0], period=date(2026, 8, 1)
    )
    assert len(events) == 1
    assert events[0].facts["direction"] == "up"


def test_threshold_cross_downward():
    events = threshold_cross_events(
        "cpi", prior_value=3.1, new_value=2.8, thresholds=[3.0], period=date(2026, 8, 1)
    )
    assert len(events) == 1
    assert events[0].facts["direction"] == "down"


def test_threshold_no_cross_when_both_sides_below():
    events = threshold_cross_events(
        "cpi", prior_value=2.1, new_value=2.4, thresholds=[3.0], period=date(2026, 8, 1)
    )
    assert events == []


def test_threshold_cross_multiple_thresholds_both_trip():
    events = threshold_cross_events(
        "unrate", prior_value=3.9, new_value=4.6, thresholds=[4.0, 4.5], period=date(2026, 8, 1)
    )
    assert len(events) == 2


def test_threshold_cross_none_without_prior_value():
    events = threshold_cross_events(
        "cpi", prior_value=None, new_value=3.1, thresholds=[3.0], period=date(2026, 8, 1)
    )
    assert events == []


# -- curve sign change, 5-day confirmation -----------------------------------------


def test_curve_sign_change_confirmed_at_exactly_5_days():
    e = curve_sign_change_event(
        "curve_10y2y", sign_change_date=date(2026, 9, 1), confirmed_through=date(2026, 9, 6)
    )
    assert e is not None


def test_curve_sign_change_not_confirmed_before_5_days():
    e = curve_sign_change_event(
        "curve_10y2y", sign_change_date=date(2026, 9, 1), confirmed_through=date(2026, 9, 5)
    )
    assert e is None


# -- extremes -----------------------------------------------------------------------


def test_is_extreme_new_high():
    assert is_extreme(values_last_12m=[3.9, 4.0, 4.1], latest_value=4.2) == "high"


def test_is_extreme_new_low():
    assert is_extreme(values_last_12m=[3.9, 4.0, 4.1], latest_value=3.8) == "low"


def test_is_extreme_within_range_is_none():
    assert is_extreme(values_last_12m=[3.9, 4.0, 4.1], latest_value=4.0) is None


def test_is_extreme_empty_history_is_none():
    assert is_extreme(values_last_12m=[], latest_value=4.0) is None


# -- regime change ------------------------------------------------------------------


def test_regime_change_detected():
    e = regime_change_event("labor", old_label="Stable", new_label="Softening", period=date(2026, 9, 1))
    assert e is not None
    assert e.facts == {"regime": "labor", "old": "Stable", "new": "Softening"}


def test_regime_no_change_returns_none():
    result = regime_change_event("labor", old_label="Stable", new_label="Stable", period=date(2026, 9, 1))
    assert result is None


# -- ranking: priority desc, then recency desc -------------------------------------


def test_rank_events_sorts_by_priority_desc():
    low = new_release_event("x", period=date(2026, 1, 1), high_priority=False, facts={})
    high = fomc_decision_event(statement_date=date(2026, 1, 1), decision="hold", target_range={})
    ranked = rank_events([low, high])
    assert ranked[0].type == "fomc_decision"


def test_rank_events_breaks_ties_by_recency():
    older = new_release_event("cpi", period=date(2026, 1, 1), high_priority=True, facts={})
    newer = new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={})
    ranked = rank_events([older, newer])
    assert ranked[0].as_of == date(2026, 8, 1)


def test_top_events_caps_at_limit():
    events = [
        new_release_event(f"ind{i}", period=date(2026, 1, i + 1), high_priority=False, facts={})
        for i in range(10)
    ]
    assert len(top_events(events, limit=8)) == 8
