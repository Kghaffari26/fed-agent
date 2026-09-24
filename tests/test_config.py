from datetime import date

from agents.macro.config import (
    DEFAULT_FOMC_DATES_TOML,
    DEFAULT_MACRO_TOML,
    load_fomc_calendar,
    load_macro_config,
)


def test_loads_real_macro_toml_with_all_spec_indicators():
    config = load_macro_config(DEFAULT_MACRO_TOML)
    ids = {ind.id for ind in config.indicators}
    # one per row of SPEC_MACRO.md §2
    expected = {
        "cpi", "core_cpi", "pce", "core_pce", "breakeven_5y",
        "unrate", "payrolls", "claims", "jolts", "ahe",
        "gdp", "retail_sales", "industrial_production",
        "fed_funds_upper", "fed_funds_lower", "effr",
        "treasury_3m", "treasury_2y", "treasury_5y", "treasury_10y", "treasury_30y",
        "curve_10y2y", "curve_10y3m", "mortgage_30y",
        "umich_sentiment",
    }
    assert ids == expected
    assert len(config.indicators) == 25


def test_settings_defaults_match_spec_8():
    config = load_macro_config(DEFAULT_MACRO_TOML)
    assert config.settings.first_run_years == 11
    assert config.settings.publish_series_years == 10
    assert config.settings.summarize_minutes is True
    assert config.settings.use_bls_fallback is False
    assert config.settings.max_events_to_llm == 8


def test_indicator_lookup_by_id():
    config = load_macro_config(DEFAULT_MACRO_TOML)
    cpi = config.indicator("cpi")
    assert cpi.fred_series == "CPIAUCSL"
    assert cpi.good_direction == "down"
    assert cpi.thresholds == [3.0, 2.5]


def test_indicator_lookup_missing_raises_keyerror():
    config = load_macro_config(DEFAULT_MACRO_TOML)
    try:
        config.indicator("does_not_exist")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_indicators_by_group():
    config = load_macro_config(DEFAULT_MACRO_TOML)
    rates = config.indicators_by_group("rates")
    assert {ind.id for ind in rates} == {
        "fed_funds_upper", "fed_funds_lower", "effr",
        "treasury_3m", "treasury_2y", "treasury_5y", "treasury_10y", "treasury_30y",
        "curve_10y2y", "curve_10y3m", "mortgage_30y",
    }


def test_payrolls_uses_thousands_scale_and_mom_diff_primary():
    config = load_macro_config(DEFAULT_MACRO_TOML)
    payrolls = config.indicator("payrolls")
    assert payrolls.primary == "mom_diff"
    assert payrolls.units_scale == "thousands"
    assert payrolls.good_direction == "up"


def test_loads_real_fomc_dates_toml():
    calendar = load_fomc_calendar(DEFAULT_FOMC_DATES_TOML)
    assert len(calendar.meetings) >= 1
    assert all(m.end >= m.start for m in calendar.meetings)


def test_fomc_calendar_next_meeting_picks_earliest_upcoming():
    calendar = load_fomc_calendar(DEFAULT_FOMC_DATES_TOML)
    upcoming = calendar.next_meeting(date(2026, 1, 1))
    assert upcoming is not None
    assert upcoming.start == min(m.start for m in calendar.meetings)


def test_fomc_calendar_next_meeting_none_when_all_past():
    calendar = load_fomc_calendar(DEFAULT_FOMC_DATES_TOML)
    far_future = date(2099, 1, 1)
    assert calendar.next_meeting(far_future) is None


def test_fomc_calendar_is_decision_day():
    calendar = load_fomc_calendar(DEFAULT_FOMC_DATES_TOML)
    a_meeting_end = calendar.meetings[0].end
    assert calendar.is_decision_day(a_meeting_end) is True
    assert calendar.is_decision_day(date(2026, 2, 2)) is False


def test_meeting_end_before_start_rejected():
    from pydantic import ValidationError

    from agents.macro.config import FomcMeeting

    try:
        FomcMeeting(start="2026-01-28", end="2026-01-27")
    except ValidationError:
        pass
    else:
        raise AssertionError("expected ValidationError")
