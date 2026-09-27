"""The agents_core.evals suites (evals/macro/suites.py) run offline with the fake
Anthropic client: the right cases, trajectory scorers wired to the loop, the
no-trigger scenarios checked for "no loop ran", and history/results written."""

from __future__ import annotations

import json

import pytest
from agents_core.evals import run_suite

from evals.macro import suites
from tests.macro_fakes import FakeAnthropic, message_text, tool_results


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS_CORE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENTS_CORE_GUARD_FAILURES_PATH", str(tmp_path / "guard_failures.jsonl"))
    monkeypatch.setenv("AGENTS_CORE_GIT_SHA", "test-sha")


def focused_investigator(params: dict) -> list[dict]:
    """The components for a CPI/jobs release, the FOMC context for a decision, then
    finish quoting a component value the tool returned."""
    messages = params["messages"]
    task = json.loads(message_text(messages[0]))
    series_id = task["trigger"]["series_id"]
    if len(messages) == 1:
        if series_id in ("cpi", "payrolls", "unrate"):
            release = "cpi" if series_id == "cpi" else "payrolls"
            return [{"type": "tool_use", "id": "a", "name": "get_components", "input": {"release": release}}]
        return [{"type": "tool_use", "id": "a", "name": "get_fomc_context", "input": {}}]
    got = tool_results(messages)
    if "get_components" in got:
        first = got["get_components"]["components"][0]
        text, cited = f"{first['name']} was {first['latest']} in the latest month.", [series_id, first["id"]]
    else:
        rng = got["get_fomc_context"]["target_range"]
        text, cited = f"The target range is {rng['lower']} to {rng['upper']} percent.", []
    return [
        {"type": "tool_use", "id": "f", "name": "finish", "input": {"analysis": text, "cited_series": cited}}
    ]


def test_investigator_cases_cover_the_five_scenarios():
    cases = {c.id: c.expected for c in suites.investigator_cases()}
    assert set(cases) == {
        "cpi_day",
        "delayed_release_day",
        "fomc_day",
        "jobs_day_big_revision",
        "quiet_day_minor_event",
    }
    assert [k for k, v in cases.items() if v["runs"]] == ["cpi_day", "fomc_day", "jobs_day_big_revision"]


def test_investigator_suite_scores_trajectories(tmp_path):
    client = FakeAnthropic(investigator=focused_investigator)
    report = run_suite(suites.INVESTIGATOR, llm_client=client, max_usd=1.0, evals_dir=tmp_path)

    by_id = {c.id: {s.name: s for s in c.scores} for c in report.cases}
    assert report.n_scored == 5
    for case_id in ("cpi_day", "jobs_day_big_revision", "fomc_day"):
        scores = by_id[case_id]
        assert scores["required_tools_called"].passed, case_id
        assert scores["forbidden_tools_not_called"].passed
        assert scores["stop_reason"].passed and scores["max_steps"].passed
        assert scores["numbers_supported"].passed, scores["numbers_supported"].detail
        assert scores["judge_quality"].value == 0.75
    for case_id in ("quiet_day_minor_event", "delayed_release_day"):
        assert all(s.passed for s in by_id[case_id].values()), case_id  # no loop ran
    # The FOMC fake cites nothing, so its citations check fails; everything else passes.
    assert not by_id["fomc_day"]["citations_valid"].passed
    assert by_id["cpi_day"]["citations_valid"].passed

    history = [json.loads(x) for x in (tmp_path / "history.jsonl").read_text().splitlines()]
    assert history[-1]["suite"] == "macro-investigator" and history[-1]["git_sha"] == "test-sha"
    assert "required_tools_called" in history[-1]["scores"]


def test_investigator_wrong_tools_fail_required_tools(tmp_path):
    # good_investigator never calls get_components: CPI and jobs days miss their required tool.
    report = run_suite(suites.INVESTIGATOR, llm_client=FakeAnthropic(), max_usd=1.0, evals_dir=tmp_path)
    by_id = {c.id: {s.name: s for s in c.scores} for c in report.cases}
    assert not by_id["cpi_day"]["required_tools_called"].passed
    assert by_id["fomc_day"]["required_tools_called"].passed


def test_brief_fomc_and_template_suites_run_offline(tmp_path):
    for suite in (suites.TEMPLATES, suites.BRIEF, suites.FOMC_READ):
        report = run_suite(suite, llm_client=FakeAnthropic(), max_usd=1.0, evals_dir=tmp_path)
        assert report.n_scored == len(suite.cases), suite.name
    assert report.scores["phrases_verbatim"] < 1.0  # the fake's invented phrase is caught
    lines = (tmp_path / "history.jsonl").read_text().splitlines()
    assert [json.loads(x)["suite"] for x in lines] == ["macro-templates", "macro-brief", "macro-fomc-read"]


def test_spend_cap_skips_remaining_cases(tmp_path):
    report = run_suite(suites.BRIEF, llm_client=FakeAnthropic(), max_usd=0.0001, evals_dir=tmp_path)
    assert report.budget_exhausted
