"""Hand-computed fixtures for every transform in SPEC_MACRO.md §5.1."""

from __future__ import annotations

from datetime import date, timedelta

from agents.macro.fetch_fred import Observation
from agents.macro.transform import (
    ann_3m_pct,
    apply_transform,
    avg_3,
    avg_4w,
    change_pp,
    level,
    mom_diff,
    mom_pct,
    rolling_mean_series,
    yoy_pct,
)


def monthly_series(values: list[float | None], start=date(2025, 8, 1)) -> list[Observation]:
    obs = []
    y, m = start.year, start.month
    for v in values:
        obs.append(Observation(date=date(y, m, 1), value=v))
        m += 1
        if m == 13:
            m = 1
            y += 1
    return obs


def quarterly_series(values: list[float | None], start_year=2024, start_q=1) -> list[Observation]:
    obs = []
    y, q = start_year, start_q
    month_by_q = {1: 1, 2: 4, 3: 7, 4: 10}
    for v in values:
        obs.append(Observation(date=date(y, month_by_q[q], 1), value=v))
        q += 1
        if q == 5:
            q = 1
            y += 1
    return obs


def weekly_series(values: list[float | None], start=date(2025, 1, 3)) -> list[Observation]:
    return [Observation(date=start + timedelta(weeks=i), value=v) for i, v in enumerate(values)]


# -- yoy_pct ------------------------------------------------------------------


def test_yoy_pct_monthly_basic():
    values = [100.0 + i for i in range(13)]  # 100..112, 13 months
    series = monthly_series(values)
    assert round(yoy_pct(series, "monthly"), 6) == round((112 / 100 - 1) * 100, 6)


def test_yoy_pct_monthly_missing_reference_month_returns_none():
    values = [100.0 + i for i in range(13)]
    values[0] = None  # the t-12 reference month is missing
    series = monthly_series(values)
    assert yoy_pct(series, "monthly") is None


def test_yoy_pct_monthly_insufficient_history_returns_none():
    series = monthly_series([100.0, 101.0, 102.0])  # only 3 months
    assert yoy_pct(series, "monthly") is None


def test_yoy_pct_quarterly_uses_t_minus_4():
    values = [100.0, 101.0, 102.0, 103.0, 104.0]  # 5 quarters
    series = quarterly_series(values)
    assert round(yoy_pct(series, "quarterly"), 6) == round((104 / 100 - 1) * 100, 6)


def test_yoy_pct_weekly_uses_nearest_observation_52_weeks_earlier():
    # 53 weekly points a year apart; the 53rd is "latest", the ~52-weeks-ago
    # point should be observation index 0 (364 days earlier, exact match).
    values = [100.0] + [None] * 51 + [110.0]
    series = weekly_series(values)
    # replace one mid-series point near the 52-week mark with a real value
    series[0] = Observation(date=series[0].date, value=90.0)
    assert round(yoy_pct(series, "weekly"), 6) == round((110 / 90 - 1) * 100, 6)


def test_yoy_pct_weekly_no_observation_within_tolerance_returns_none():
    # only two points, nowhere near 52 weeks apart
    series = weekly_series([100.0, 101.0])
    assert yoy_pct(series, "weekly") is None


# -- mom_pct / mom_diff --------------------------------------------------------


def test_mom_pct_basic():
    series = monthly_series([100.0, 103.0])
    assert round(mom_pct(series), 6) == 3.0


def test_mom_pct_missing_prior_returns_none():
    series = monthly_series([None, 103.0])
    assert mom_pct(series) is None


def test_mom_diff_payrolls_style_thousands():
    series = monthly_series([159853.0, 159995.0])
    assert mom_diff(series) == 142.0


def test_mom_diff_negative():
    series = monthly_series([159995.0, 159853.0])
    assert mom_diff(series) == -142.0


# -- ann_3m_pct -----------------------------------------------------------------


def test_ann_3m_pct_basic():
    series = monthly_series([100.0, 100.5, 101.0, 101.5])  # t-3 .. t
    expected = ((101.5 / 100.0) ** 4 - 1) * 100
    assert round(ann_3m_pct(series), 6) == round(expected, 6)


def test_ann_3m_pct_insufficient_history_returns_none():
    series = monthly_series([100.0, 101.0])
    assert ann_3m_pct(series) is None


# -- avg_3 / avg_4w ---------------------------------------------------------------


def test_avg_3_basic():
    series = monthly_series([10.0, 20.0, 30.0, 60.0])
    assert round(avg_3(series), 6) == round((20.0 + 30.0 + 60.0) / 3, 6)


def test_avg_3_skips_none_and_walks_further_back():
    series = monthly_series([10.0, 20.0, None, 60.0])
    # last 3 non-None values walking backward from the end: 60, 20, 10
    assert avg_3(series) == 30.0


def test_avg_3_insufficient_non_none_values_returns_none():
    series = monthly_series([None, None, 60.0])
    assert avg_3(series) is None


def test_avg_4w_basic():
    series = weekly_series([200.0, 210.0, 220.0, 230.0])
    assert avg_4w(series) == 215.0


# -- change_pp / level -----------------------------------------------------------


def test_change_pp_basic():
    series = monthly_series([4.06, 4.00])
    assert round(change_pp(series), 6) == -0.06


def test_level_returns_latest():
    series = monthly_series([1.0, 2.0, 3.0])
    assert level(series) == 3.0


def test_level_latest_none_returns_none():
    series = monthly_series([1.0, 2.0, None])
    assert level(series) is None


# -- apply_transform dispatch -----------------------------------------------------


def test_apply_transform_dispatches_to_yoy_pct():
    series = monthly_series([100.0 + i for i in range(13)])
    assert apply_transform("yoy_pct", series, "monthly") == yoy_pct(series, "monthly")


def test_apply_transform_dispatches_to_level():
    series = monthly_series([1.0, 2.0, 3.0])
    assert apply_transform("level", series, "monthly") == 3.0


# -- rolling_mean_series ------------------------------------------------------------


def test_rolling_mean_series_basic():
    series = monthly_series([3.0, 6.0, 9.0, 12.0])
    means = rolling_mean_series(series, window=3)
    assert means == [None, None, 6.0, 9.0]


def test_rolling_mean_series_none_when_window_contains_missing():
    series = monthly_series([3.0, None, 9.0])
    means = rolling_mean_series(series, window=3)
    assert means == [None, None, None]
