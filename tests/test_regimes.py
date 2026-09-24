"""Boundary-value tests for every regime rule in SPEC_MACRO.md §5.4."""

from __future__ import annotations

from datetime import date

from agents.macro.fetch_fred import Observation
from agents.macro.transform import (
    curve_last_sign_change,
    curve_regime,
    growth_regime,
    inflation_regime,
    labor_regime,
)

# -- inflation regime: Cooling / Heating / Steady, band = yoy +/- 0.3 -----------


def test_inflation_steady_when_within_band():
    assert inflation_regime(core_pce_yoy=2.9, core_pce_ann_3m=2.9) == "Steady"


def test_inflation_cooling_when_ann3m_below_band():
    assert inflation_regime(core_pce_yoy=2.9, core_pce_ann_3m=2.5) == "Cooling"


def test_inflation_cooling_at_exact_boundary_is_steady_not_cooling():
    # rule is strict "<", so exactly yoy - 0.3 is still Steady
    assert inflation_regime(core_pce_yoy=2.9, core_pce_ann_3m=2.6) == "Steady"


def test_inflation_cooling_just_past_boundary():
    assert inflation_regime(core_pce_yoy=2.9, core_pce_ann_3m=2.5999) == "Cooling"


def test_inflation_heating_when_ann3m_above_band():
    assert inflation_regime(core_pce_yoy=2.4, core_pce_ann_3m=2.9) == "Heating"


def test_inflation_heating_at_exact_boundary_is_steady():
    assert inflation_regime(core_pce_yoy=2.4, core_pce_ann_3m=2.7) == "Steady"


# -- labor regime: Sahm-style ----------------------------------------------------


def _unrate_avg3_series(current: float, trailing_min: float) -> list[float | None]:
    # 13 months: months -12..-1 hover at `trailing_min`, current month is `current`
    return [trailing_min] * 12 + [current]


def test_labor_stable_when_sahm_below_softening_threshold():
    series = _unrate_avg3_series(current=4.0, trailing_min=3.8)  # delta 0.2
    assert labor_regime(series, payrolls_avg3=100.0) == "Stable"


def test_labor_softening_at_sahm_boundary_030():
    series = _unrate_avg3_series(current=4.1, trailing_min=3.8)  # delta 0.3 exactly
    assert labor_regime(series, payrolls_avg3=100.0) == "Softening"


def test_labor_sahm_triggered_at_boundary_050():
    series = _unrate_avg3_series(current=4.3, trailing_min=3.8)  # delta 0.5 exactly
    assert labor_regime(series, payrolls_avg3=100.0) == "Sahm rule triggered"


def test_labor_sahm_triggered_above_boundary():
    series = _unrate_avg3_series(current=4.5, trailing_min=3.8)  # delta 0.7
    assert labor_regime(series, payrolls_avg3=100.0) == "Sahm rule triggered"


def test_labor_softening_via_weak_payrolls_even_if_sahm_stable():
    series = _unrate_avg3_series(current=4.0, trailing_min=3.9)  # delta 0.1, not softening on its own
    assert labor_regime(series, payrolls_avg3=45.0) == "Softening"


def test_labor_payrolls_exactly_at_threshold_is_not_softening():
    series = _unrate_avg3_series(current=4.0, trailing_min=3.9)
    assert labor_regime(series, payrolls_avg3=50.0) == "Stable"


def test_labor_insufficient_history_falls_back_to_payrolls_rule():
    assert labor_regime([4.0], payrolls_avg3=30.0) == "Softening"
    assert labor_regime([4.0], payrolls_avg3=80.0) == "Stable"


# -- growth regime ----------------------------------------------------------------


def test_growth_contracting_below_zero():
    assert growth_regime(-0.1) == "Contracting"


def test_growth_slow_at_zero_boundary():
    assert growth_regime(0.0) == "Slow"


def test_growth_slow_at_upper_boundary():
    assert growth_regime(1.5) == "Slow"


def test_growth_moderate_just_above_slow():
    assert growth_regime(1.51) == "Moderate"


def test_growth_moderate_at_upper_boundary():
    assert growth_regime(3.0) == "Moderate"


def test_growth_strong_above_three():
    assert growth_regime(3.1) == "Strong"


# -- curve regime -----------------------------------------------------------------


def test_curve_inverted_below_zero():
    assert curve_regime(-0.1) == "Inverted"


def test_curve_flat_at_zero():
    assert curve_regime(0.0) == "Flat"


def test_curve_flat_at_upper_boundary():
    assert curve_regime(0.5) == "Flat"


def test_curve_normal_above_boundary():
    assert curve_regime(0.51) == "Normal"


def test_curve_last_sign_change_detects_flip():
    series = [
        Observation(date=date(2024, 1, 1), value=-0.2),
        Observation(date=date(2024, 6, 1), value=-0.1),
        Observation(date=date(2024, 9, 6), value=0.1),  # flips positive here
        Observation(date=date(2025, 1, 1), value=0.3),
    ]
    assert curve_last_sign_change(series) == date(2024, 9, 6)


def test_curve_last_sign_change_none_when_no_flip():
    series = [
        Observation(date=date(2024, 1, 1), value=0.2),
        Observation(date=date(2024, 6, 1), value=0.3),
    ]
    assert curve_last_sign_change(series) is None


def test_curve_last_sign_change_ignores_zero_and_missing():
    series = [
        Observation(date=date(2024, 1, 1), value=-0.1),
        Observation(date=date(2024, 3, 1), value=None),
        Observation(date=date(2024, 5, 1), value=0.0),
        Observation(date=date(2024, 7, 1), value=0.2),  # first real flip
    ]
    assert curve_last_sign_change(series) == date(2024, 7, 1)
