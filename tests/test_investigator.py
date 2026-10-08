"""The release investigator (§6.1): trigger choice, its tools on real recorded FRED data,
the deterministic fallback, and deterministic replays of real agent-loop trajectories
recorded during the 2026-09-27 eval run (agents_core.agent_loop.ReplayClient)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from agents_core.agent_loop import ReplayClient, ToolError
from agents_core.costs import CostTracker
from agents_core.guards import verify_numbers
from agents_core.llm import LLM

from agents.macro import investigate as inv
from agents.macro.events import Event
from evals.macro import suites

TRAJECTORIES = Path(__file__).parent / "fixtures" / "investigator"
CPI_DAY = date(2026, 9, 11)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS_CORE_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture(scope="module")
def cpi_data() -> inv.InvestigatorData:
    return suites.investigator_data(CPI_DAY)


def _event(event_id: str, event_type: str, priority: int = 80) -> Event:
    return Event(id=event_id, type=event_type, priority=priority, facts={"x": 1})


def test_pick_trigger_takes_the_first_high_priority_release_or_decision():
    events = [
        _event("regime_change:policy:2026-09-16", "regime_change", 85),
        _event("new_release:claims:2026-09-13", "new_release", 60),
        _event("new_release:cpi:2026-08-01", "new_release", 80),
    ]
    assert inv.pick_trigger(events).event_id == "new_release:cpi:2026-08-01"
    fomc = [_event("fomc_decision:2026-09-16", "fomc_decision", 100), *events]
    trigger = inv.pick_trigger(fomc)
    assert trigger.type == "fomc_decision" and trigger.indicator_id is None


def test_no_trigger_for_revisions_delays_or_minor_releases():
    events = [
        _event("revision:payrolls:2026-07-01", "revision"),
        _event("delayed:gdp:2026-08-26", "delayed"),
        _event("new_release:umich_sentiment:2026-08-01", "new_release"),
    ]
    assert inv.pick_trigger(events) is None


def test_components_view_on_real_cpi_data(cpi_data):
    view = inv.components_view(cpi_data, "cpi")
    by_id = {c["id"]: c for c in view["components"]}
    assert set(by_id) == {"cpi_shelter", "cpi_energy", "cpi_food", "cpi_core_goods", "cpi_core_services"}
    assert by_id["cpi_energy"]["latest"] == 16.0 and by_id["cpi_energy"]["MoM"] == 2.1
    assert by_id["cpi_shelter"]["period"] == "2026-08-01"


def test_percentile_and_cycles_on_real_cpi_data(cpi_data):
    pct = inv.percentile_view(cpi_data, "cpi", 3.4, 10)
    assert pct["percentile"] == 72 and pct["history_max"] == 9.0
    cycles = inv.cycles_view(cpi_data, "cpi")
    assert cycles["current"] == {"period": "2026-08-01", "value": 3.4}
    assert cycles["windows"]["2019"]["average"] == 1.8
    assert cycles["windows"]["2022-23"]["high"] == 9.0


def test_series_view_samples_long_ranges_quarterly(cpi_data):
    short = inv.series_view(cpi_data, "cpi", "1y")
    long = inv.series_view(cpi_data, "cpi", "10y")
    # Sep 2025 - Aug 2026, less the real Oct-2025 shutdown gap (skipped, never imputed).
    assert [d for d, _ in short["observations"]][:2] == ["2025-11-01", "2025-12-01"]
    assert len(short["observations"]) == 10
    assert len(long["observations"]) < 45 and long["latest"] == short["latest"]


def test_unknown_series_is_a_clean_tool_error(cpi_data):
    with pytest.raises(ToolError, match="Unknown series id"):
        inv.series_view(cpi_data, "CPIAUCSL")
    with pytest.raises(ToolError, match="No components"):
        inv.components_view(cpi_data, "gdp")


def test_template_is_built_from_data(cpi_data):
    trigger = inv.Trigger("new_release:cpi:2026-08-01", "new_release", "cpi", {})
    draft = inv.template_investigation(cpi_data, trigger)
    assert draft.analysis.startswith("CPI (all items) (YoY) was 3.4%, after 3.3% the prior period.")
    assert "energy 16.0%" in draft.analysis
    assert draft.cited_series[0] == "cpi" and len(draft.cited_series) == 6


def test_no_llm_goes_straight_to_the_template(cpi_data):
    trigger = inv.Trigger("new_release:cpi:2026-08-01", "new_release", "cpi", {})
    result = inv.investigate(None, cpi_data, trigger)
    assert result.narrative_source == "template" and result.loop is None


def test_overlong_analysis_is_trimmed_to_whole_sentences():
    draft = inv.InvestigationDraft(analysis=("Word " * 70 + "end. ") * 2, cited_series=[])
    trimmed = inv._trim(draft)
    assert len(trimmed.analysis.split()) <= inv.MAX_WORDS and trimmed.analysis.endswith("end.")


@pytest.mark.parametrize(
    ("case_id", "tools"),
    [
        ("cpi_day", ["get_components", "get_series", "percentile_vs_history"]),
        ("jobs_day_big_revision", ["get_components", "get_series", "percentile_vs_history"]),
        ("fomc_day", ["get_fomc_context", "get_series", "get_series", "get_series"]),
    ],
)
def test_real_trajectories_replay_deterministically(case_id, tools):
    """A recorded real run, replayed offline: same tool calls, same result, and the
    finished analysis passes the number guard against the tool outputs."""
    data, trigger = suites.investigator_setup(case_id)

    client = ReplayClient(TRAJECTORIES / f"{case_id}.trajectory.json")
    llm = LLM(CostTracker(agent="macro", run_id="replay"), client=client)
    result = inv.investigate(llm, data, trigger)

    assert result.loop.ok and result.loop.stop_reason == "finished"
    assert result.loop.tools_called() == tools
    assert result.narrative_source == "llm"
    assert result.loop.steps <= inv.MAX_STEPS
    assert verify_numbers(result.draft.analysis, result.facts, allow=inv.GUARD_ALLOW).ok
    assert result.cited_series and set(result.cited_series) <= result.series_seen
    assert client.remaining == 0
