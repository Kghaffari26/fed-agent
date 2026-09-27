"""agents.macro.build and agents.macro.display: publish-time rounding, release timing,
calendar, key stats, yield curve, FOMC cross-check, and the once-only events."""

from __future__ import annotations

from datetime import date, timedelta

from agents.macro.build import (
    ReleaseTiming,
    build_calendar,
    build_yield_curve,
    crosscheck_target_range,
    curve_sign_change_events,
    delayed_events,
    period_label,
    regime_events,
    release_timing,
)
from agents.macro.config import load_macro_config
from agents.macro.display import display_for, displayed_delta, units_display
from agents.macro.fetch_fred import Observation
from agents.macro.schema import FomcLatest, FomcTargetRange, FomcVotes, Regime, Regimes
from agents.macro.transform import (
    confirmed_sign_change,
    inversion_periods,
    resample_month_end,
    resample_weekly_friday,
)

CONFIG = load_macro_config()


def ind(indicator_id):
    return CONFIG.indicator(indicator_id)


def daily(values, start=date(2026, 1, 5)):
    out, d = [], start
    for v in values:
        while d.weekday() > 4:
            d += timedelta(days=1)
        out.append(Observation(d, v))
        d += timedelta(days=1)
    return out


# -- display ---------------------------------------------------------------------------


def test_display_formats_follow_config():
    assert display_for(ind("cpi"), "yoy_pct").format == "percent"
    assert display_for(ind("cpi"), "mom_pct").format == "percent_signed"
    assert display_for(ind("payrolls"), "mom_diff").format == "count_signed_thousands"
    assert display_for(ind("payrolls"), "avg_3").format == "count_signed_thousands"
    assert display_for(ind("treasury_10y"), "level").decimals == 2
    assert display_for(ind("treasury_10y"), "change_pp").format == "pp_signed"
    assert display_for(ind("curve_10y2y"), "level").format == "pp_signed"
    assert display_for(ind("claims"), "level").format == "count"
    assert units_display(ind("payrolls")) == "thousands"


def test_display_rounding():
    assert display_for(ind("payrolls"), "mom_diff").round(161.6) == 162
    assert isinstance(display_for(ind("payrolls"), "mom_diff").round(161.6), int)
    assert display_for(ind("jolts"), "level").round(7271.0) == 7.3  # thousands -> millions
    assert display_for(ind("treasury_10y"), "level").round(5.184) == 5.18
    assert display_for(ind("cpi"), "yoy_pct").round(-0.04) == 0.0
    assert display_for(ind("cpi"), "yoy_pct").round(None) is None


def test_displayed_delta_matches_displayed_values():
    # 3.36 vs 3.31 displays as 3.4 vs 3.3: the delta must be 0.1, not round(0.05) = 0.0.
    assert displayed_delta(ind("cpi"), 3.36, 3.31) == 0.1
    assert displayed_delta(ind("payrolls"), 161.6, 21.2) == 141
    assert displayed_delta(ind("cpi"), 3.3, None) is None


def test_period_labels():
    assert period_label(date(2026, 8, 1), "monthly") == "Aug 2026"
    assert period_label(date(2026, 4, 1), "quarterly") == "Q2 2026"
    assert period_label(date(2026, 9, 4), "daily") == "Sep 4, 2026"


# -- release timing (§3, §5.5) ------------------------------------------------------------


def test_release_timing_next_release_and_not_delayed():
    t = release_timing(
        frequency="monthly",
        last_updated=date(2026, 9, 11),
        release_dates=[date(2026, 8, 12), date(2026, 9, 11), date(2026, 10, 14)],
        today=date(2026, 9, 26),
    )
    assert t.next_release == date(2026, 10, 14)
    assert t.delayed is False
    assert t.released_at == date(2026, 9, 11)


def test_release_timing_delayed_after_two_day_grace():
    """A shutdown-style miss: the scheduled date passed and FRED published nothing."""
    dates = [date(2026, 8, 26), date(2026, 9, 30)]
    published = [date(2026, 7, 30)]
    late = release_timing(
        frequency="quarterly",
        last_updated=date(2026, 7, 30),
        release_dates=dates,
        published_dates=published,
        today=date(2026, 8, 29),
    )
    grace = release_timing(
        frequency="quarterly",
        last_updated=date(2026, 7, 30),
        release_dates=dates,
        published_dates=published,
        today=date(2026, 8, 28),
    )
    assert late.delayed is True and late.last_scheduled == date(2026, 8, 26)
    assert grace.delayed is False


def test_gdp_release_published_without_revising_the_series_is_not_delayed():
    """The real 2026-09-26 case: FRED's GDP release (53) published on 2026-08-26, but
    A191RL1Q225SBEA's last_updated stayed 2026-07-30. Not delayed."""
    t = release_timing(
        frequency="quarterly",
        last_updated=date(2026, 7, 30),
        release_dates=[date(2026, 7, 30), date(2026, 8, 26), date(2026, 9, 30)],
        published_dates=[date(2026, 7, 30), date(2026, 8, 26)],
        today=date(2026, 9, 26),
    )
    assert t.delayed is False
    assert t.next_release == date(2026, 9, 30)


def test_scheduled_not_yet_due_is_not_delayed():
    """2026-09-30 is on FRED's calendar but not yet published: not delayed, before or
    on the day, whatever the series' last update."""
    for today in (date(2026, 9, 29), date(2026, 9, 30)):
        t = release_timing(
            frequency="quarterly",
            last_updated=date(2026, 7, 30),
            release_dates=[date(2026, 8, 26), date(2026, 9, 30)],
            published_dates=[date(2026, 8, 26)],
            today=today,
        )
        assert t.delayed is False


def test_unknown_release_status_never_flags_delayed():
    t = release_timing(
        frequency="quarterly",
        last_updated=date(2026, 7, 30),
        release_dates=[date(2026, 8, 26)],
        published_dates=None,
        today=date(2026, 9, 26),
    )
    assert t.delayed is False


def test_release_timing_release_day_before_data_posts():
    t = release_timing(
        frequency="monthly",
        last_updated=date(2026, 8, 12),
        release_dates=[date(2026, 9, 11)],
        today=date(2026, 9, 11),
    )
    assert t.next_release == date(2026, 9, 11)
    assert t.delayed is False


def test_daily_series_are_never_delayed():
    t = release_timing(
        frequency="daily",
        last_updated=date(2026, 9, 1),
        release_dates=[date(2026, 9, 20)],
        today=date(2026, 9, 26),
    )
    assert t.delayed is False


def test_delayed_event_fires_only_on_transition():
    timing = ReleaseTiming(
        released_at=None, next_release=None, delayed=True, last_scheduled=date(2026, 8, 26)
    )
    first = delayed_events({"gdp": timing}, set(), {"gdp": "Real GDP growth"})
    again = delayed_events({"gdp": timing}, {"gdp"}, {"gdp": "Real GDP growth"})
    assert [e.id for e in first] == ["delayed:gdp:2026-08-26"]
    assert first[0].facts["indicator"] == "Real GDP growth"
    assert again == []


# -- calendar ------------------------------------------------------------------------------


def test_calendar_window_and_grouping():
    releases = {
        "10": (
            "Consumer Price Index",
            [date(2026, 9, 11), date(2026, 10, 14), date(2026, 12, 10)],
            ["core_cpi", "cpi"],
        ),
        "50": ("Employment Situation", [date(2026, 10, 2)], ["unrate", "payrolls"]),
    }
    cal = build_calendar(releases, today=date(2026, 9, 26))
    assert [(c.date, c.release) for c in cal] == [
        (date(2026, 10, 2), "Employment Situation"),
        (date(2026, 10, 14), "Consumer Price Index"),
    ]
    assert cal[0].indicator_ids == ["payrolls", "unrate"]


# -- yield curve & curve events ---------------------------------------------------------------


def test_resample_weekly_friday_and_month_end():
    obs = daily([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], start=date(2026, 9, 21))  # Mon 21 .. Tue 29
    weekly = resample_weekly_friday(obs)
    assert [(o.date, o.value) for o in weekly] == [(date(2026, 9, 25), 5.0), (date(2026, 10, 2), 7.0)]
    monthly = resample_month_end(obs)
    assert [o.value for o in monthly] == [7.0]


def test_inversion_periods_need_five_readings():
    spread = daily([0.2, -0.1, -0.2, 0.1, -0.1, -0.1, -0.1, -0.1, -0.1, 0.3, -0.2])
    periods = inversion_periods(spread)
    assert len(periods) == 1
    assert periods[0][0] == spread[4].date and periods[0][1] == spread[8].date


def test_confirmed_sign_change_and_event_once():
    spread = daily([0.3, 0.2, -0.1, -0.2, -0.1, -0.3, -0.2, -0.1])
    change, confirmed = confirmed_sign_change(spread)
    assert change == spread[2].date and confirmed == spread[6].date

    before_confirmation = {"T10Y2Y": spread[5].date}
    events = curve_sign_change_events({"T10Y2Y": spread}, before_confirmation)
    assert len(events) == 1 and events[0].facts["new_sign"] == "negative"
    assert curve_sign_change_events({"T10Y2Y": spread}, {"T10Y2Y": spread[-1].date}) == []
    assert curve_sign_change_events({"T10Y2Y": spread}, {"T10Y2Y": None}) == []  # first run


def test_unconfirmed_sign_change_is_ignored():
    assert confirmed_sign_change(daily([0.3, 0.2, -0.1, -0.2, 0.1])) is None


def test_build_yield_curve_shape():
    days = 600
    obs = {
        "DGS2": daily([4.0] * days),
        "DGS10": daily([4.5] * days),
        "T10Y2Y": daily([0.5] * days),
        "DGS3MO": daily([4.2] * days),
        "DGS5": daily([4.3] * days),
        "DGS30": daily([4.8] * days),
    }
    curve = build_yield_curve(obs, publish_years=10)
    n = len(curve.series.dates)
    assert n == len(curve.series.y2) == len(curve.series.y10) == len(curve.series.spread_10y2y)
    assert all(d.weekday() == 4 for d in curve.series.dates)
    assert [p.tenor for p in curve.snapshot] == ["3M", "2Y", "5Y", "10Y", "30Y"]
    assert build_yield_curve({"DGS2": obs["DGS2"]}, publish_years=10) is None


# -- FOMC cross-check (§5.7 step 3) ------------------------------------------------------------


def _block(lower=3.75, upper=4.0):
    return FomcLatest(
        date=date(2026, 9, 16),
        url="https://example.invalid",
        decision="hike",
        target_range=FomcTargetRange(lower=lower, upper=upper),
        change_bp=25,
        votes=FomcVotes(for_count=12),
        latest_text="x",
    )


def test_crosscheck_pending_until_fred_updates():
    upper = [Observation(date(2026, 9, 16), 3.75)]
    lower = [Observation(date(2026, 9, 16), 3.5)]
    block, warning = crosscheck_target_range(_block(), upper, lower)
    assert block.crosscheck_pending is True and warning is None


def test_crosscheck_confirms_matching_range():
    block, warning = crosscheck_target_range(
        _block(), [Observation(date(2026, 9, 17), 4.0)], [Observation(date(2026, 9, 17), 3.75)]
    )
    assert block.crosscheck_pending is False and warning is None


def test_crosscheck_mismatch_trusts_fred():
    block, warning = crosscheck_target_range(
        _block(), [Observation(date(2026, 9, 17), 4.25)], [Observation(date(2026, 9, 17), 4.0)]
    )
    assert block.target_range == FomcTargetRange(lower=4.0, upper=4.25)
    assert "using FRED" in warning


# -- regime events ------------------------------------------------------------------------------


def _regimes(labor="Stable"):
    r = Regime(label="Steady", detail="")
    return Regimes(inflation=r, labor=Regime(label=labor, detail=""), growth=r, policy=r, curve=r)


def test_regime_change_only_against_previous_labels():
    assert regime_events({}, _regimes("Softening"), today=date(2026, 9, 26)) == []  # first run
    events = regime_events({"labor": "Stable"}, _regimes("Softening"), today=date(2026, 9, 26))
    assert [(e.facts["old"], e.facts["new"]) for e in events] == [("Stable", "Softening")]
