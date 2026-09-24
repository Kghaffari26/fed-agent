"""§4 state.json read/write, trimming, and the has_series_changed no-op path."""

from __future__ import annotations

import json
from datetime import date

from agents.macro.fetch_fred import Observation, has_series_changed
from agents.macro.state import (
    FomcState,
    LastBrief,
    MacroState,
    SeriesState,
    load_state,
    save_state,
    trim_observations,
    update_series_state,
)


def test_load_state_missing_file_returns_empty_state(tmp_path):
    state = load_state(tmp_path / "does_not_exist.json")
    assert state.series == {}
    assert state.fomc.latest_statement_date is None
    assert state.last_brief is None


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "state.json"
    payrolls = SeriesState(last_updated="2026-09-05 07:46:02-05", observations={"2026-08-01": 159995.0})
    state = MacroState(
        series={"PAYEMS": payrolls},
        fomc=FomcState(latest_statement_date="2026-09-16", latest_minutes_date="2026-08-20"),
        last_brief=LastBrief(
            run_id="run-1",
            bullets=["CPI rose 2.9%."],
            event_ids=["new_release:CPIAUCSL:2026-08"],
        ),
    )
    save_state(state, path)
    reloaded = load_state(path)
    assert reloaded.series["PAYEMS"].last_updated == "2026-09-05 07:46:02-05"
    assert reloaded.series["PAYEMS"].observations == {"2026-08-01": 159995.0}
    assert reloaded.fomc.latest_statement_date == "2026-09-16"
    assert reloaded.last_brief.run_id == "run-1"


def test_save_state_matches_spec_json_shape(tmp_path):
    path = tmp_path / "state.json"
    state = MacroState(
        series={"PAYEMS": SeriesState(last_updated="2026-09-05", observations={"2026-08-01": 159995.0})},
    )
    save_state(state, path)
    raw = json.loads(path.read_text())
    assert set(raw.keys()) == {"series", "fomc", "last_brief"}
    assert set(raw["fomc"].keys()) == {"latest_statement_date", "latest_minutes_date"}


def test_trim_observations_keeps_only_last_36():
    observations = {f"2020-{m:02d}-01": float(m) for m in range(1, 13)}
    observations.update({f"2021-{m:02d}-01": float(m) for m in range(1, 13)})
    observations.update({f"2022-{m:02d}-01": float(m) for m in range(1, 13)})
    observations.update({f"2023-{m:02d}-01": float(m) for m in range(1, 13)})  # 48 total
    trimmed = trim_observations(observations)
    assert len(trimmed) == 36
    assert "2020-01-01" not in trimmed  # oldest dropped
    assert "2023-12-01" in trimmed  # newest kept


def test_trim_observations_noop_under_limit():
    observations = {"2026-01-01": 1.0, "2026-02-01": 2.0}
    assert trim_observations(observations) == observations


def test_update_series_state_merges_and_trims():
    state = MacroState()
    state.series["CPIAUCSL"] = SeriesState(
        last_updated="2026-08-13", observations={"2026-07-01": 321.3}
    )
    update_series_state(
        state,
        "CPIAUCSL",
        last_updated="2026-09-11",
        new_observations=[Observation(date=date(2026, 8, 1), value=322.5)],
    )
    updated = state.series["CPIAUCSL"]
    assert updated.last_updated == "2026-09-11"
    assert updated.observations == {"2026-07-01": 321.3, "2026-08-01": 322.5}


def test_series_last_updated_helper():
    state = MacroState(series={"CPIAUCSL": SeriesState(last_updated="2026-09-11", observations={})})
    assert state.series_last_updated("CPIAUCSL") == "2026-09-11"
    assert state.series_last_updated("UNKNOWN") is None


# -- the no-change path: zero fetches/LLM calls when last_updated is unchanged --


def test_no_change_path_skips_when_last_updated_matches():
    unchanged = SeriesState(last_updated="2026-09-11 07:47:03-05", observations={})
    state = MacroState(series={"CPIAUCSL": unchanged})
    # simulating what the runner does before deciding whether to call
    # fetch_observations or the LLM at all (§3's change-detection rule)
    stored = state.series_last_updated("CPIAUCSL")
    assert has_series_changed(stored, "2026-09-11 07:47:03-05") is False


def test_no_change_path_triggers_fetch_when_last_updated_differs():
    stale = SeriesState(last_updated="2026-08-13 07:47:03-05", observations={})
    state = MacroState(series={"CPIAUCSL": stale})
    stored = state.series_last_updated("CPIAUCSL")
    assert has_series_changed(stored, "2026-09-11 07:47:03-05") is True
