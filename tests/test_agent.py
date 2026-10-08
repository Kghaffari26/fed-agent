"""End-to-end runs of the macro agent through the real agents-core runner
(`agents_core.runner.run`), with FRED and federalreserve.gov served by an
httpx.MockTransport and a fake Anthropic client. Covers SPEC_MACRO.md §13:
publish contract, zero LLM calls on an immediate re-run, the payroll revision,
the forced guard failure, the dry run, and the cost cap.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from agents_core import registry, runner
from agents_core.http import Http

from agents.macro.agent import AGENT, MacroAgent, state_path
from agents.macro.schema import MacroOutput
from tests.macro_fakes import FakeAnthropic, FakeFred, fake_transport, good_investigator


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS_CORE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENTS_CORE_PUBLISH_DIR", str(tmp_path / "public-data"))
    monkeypatch.setenv("MACRO_OBS_CACHE_DIR", str(tmp_path / "obs"))
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    monkeypatch.setenv("AGENTS_CORE_MAX_RUN_USD", "0.25")
    return tmp_path


@pytest.fixture
def fred():
    return FakeFred(today=datetime.now(UTC).date())


def _run(env, fred, client, **kwargs) -> int:
    http = Http(cache_dir=env / "http", transport=fake_transport(fred), sleep=lambda s: None)
    try:
        return runner.run(AGENT, http=http, llm_client=client, **kwargs)
    finally:
        http.close()


def _latest(env) -> dict:
    return json.loads((env / "public-data" / "latest.json").read_text())


def _cost_lines(env) -> list[dict]:
    path = env / "data" / "costs.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_registered_via_entry_point():
    assert registry.discover_agents()["macro"] == "agents.macro.agent:AGENT"
    assert isinstance(registry.load("macro"), MacroAgent)


def test_first_run_publishes_the_data_branch_contract(env, fred):
    client = FakeAnthropic()
    assert _run(env, fred, client) == 0

    pub = env / "public-data"
    for name in (
        "latest.json",
        "manifest-entry.json",
        "costs-summary.json",
        "schema.json",
        "trace.json",
        "trace.schema.json",
    ):
        assert (pub / name).is_file(), name
    assert len(list((pub / "history").glob("*.json"))) == 1
    assert (pub / "latest.json").stat().st_size < 350_000

    output = MacroOutput.model_validate(_latest(env))
    assert output.meta.agent == "macro"
    assert output.meta.data_changed is True
    assert output.meta.cost_usd > 0
    assert len(output.indicators) == 25
    assert output.brief.narrative_source == "llm"
    assert output.brief.bullets and all(b.citations for b in output.brief.bullets)
    assert output.headline == "The FOMC raised rates to 3.75–4.00%."

    latest = output.fomc.latest
    assert latest.date == date(2026, 9, 16) and latest.decision == "hike" and latest.change_bp == 25
    assert latest.previous_date == date(2026, 7, 29)
    assert latest.read.narrative_source == "llm"
    assert latest.read.tone_shift == "more_hawkish"
    assert set(latest.read.cited_change_idx) <= {c.idx for c in latest.changes}
    assert [kp.phrase in latest.latest_text for kp in latest.read.key_phrases] == [True]  # bad phrase dropped
    assert latest.crosscheck_pending is False  # FRED target range already shows 3.75-4.00
    assert output.fomc.minutes.summary.startswith("Participants")
    assert output.fomc.next_meeting.start == date(2026, 10, 27)

    manifest = json.loads((pub / "manifest-entry.json").read_text())
    assert manifest["id"] == "macro" and manifest["route"] == "/macro" and manifest["status"] == "ok"
    assert manifest["expected_interval_hours"] == 24
    assert len(manifest["key_stats"]) == 4

    purposes = [line["purpose"] for line in _cost_lines(env) if "purpose" in line]
    assert purposes == [
        "macro:brief",
        "macro:fomc_read",
        "macro:minutes",
        "macro:investigator:step1",
        "macro:investigator:step2",
    ]
    assert state_path().is_file()

    # §6.1: the FOMC decision triggered the release investigator.
    inv = output.investigation
    assert inv.trigger.event_id == "fomc_decision:2026-09-16" and inv.trigger.type == "fomc_decision"
    assert inv.narrative_source == "llm" and inv.model
    assert inv.analysis.startswith("Core PCE was ")
    assert [c.id for c in inv.cited_series] == ["core_pce"]  # the unknown id was dropped by code
    assert inv.cited_series[0].url == "https://fred.stlouisfed.org/series/PCEPILFE"
    assert inv.loop.tool_calls == ["get_series", "get_fomc_context"]
    assert inv.loop.stop_reason == "finished" and inv.loop.steps == 2 and inv.loop.cost_usd > 0
    assert output.meta.warnings == []

    # trace.json: the run, its phases, every LLM call, the loop and its tool calls.
    trace = json.loads((pub / "trace.json").read_text())
    kinds = [s["kind"] for s in trace["spans"]]
    assert kinds.count("agent_loop") == 1 and kinds.count("tool_call") == 2
    assert {s["name"] for s in trace["spans"] if s["kind"] == "phase"} >= {"fetch", "transform", "analyze"}
    assert any(s["name"] == "macro:investigate" for s in trace["spans"])
    assert manifest["trace_summary"]["tool_calls"] == 2 and manifest["trace_summary"]["llm_calls"] == 5
    assert "test-key" not in (pub / "trace.json").read_text()


def test_immediate_rerun_makes_zero_llm_calls(env, fred):
    first_client = FakeAnthropic()
    assert _run(env, fred, first_client) == 0
    first = _latest(env)

    second_client = FakeAnthropic()
    assert _run(env, fred, second_client) == 0
    second = _latest(env)

    assert second_client.calls == []
    assert second["meta"]["data_changed"] is False
    assert second["meta"]["cost_usd"] == 0
    assert second["events"] == []
    assert second["brief"]["reused_from_run_id"] == first["meta"]["run_id"]
    assert second["brief"]["bullets"] == first["brief"]["bullets"]
    assert second["headline"] == first["headline"]
    assert second["fomc"]["latest"]["read"] == first["fomc"]["latest"]["read"]
    assert second["fomc"]["minutes"] == first["fomc"]["minutes"]
    # The investigation is carried over, marked as reused.
    assert second["investigation"]["analysis"] == first["investigation"]["analysis"]
    assert second["investigation"]["reused_from_run_id"] == first["meta"]["run_id"]
    # Unchanged series skip the observations call entirely (§3).
    second_run_requests = fred.requests[len(fred.requests) // 2 :]
    assert not any("/series/observations" in r for r in second_run_requests)
    runs = [line for line in _cost_lines(env) if line.get("kind") == "run"]
    assert [r["calls"] for r in runs] == [5, 0]


def test_payroll_revision_shows_up_as_revision_block_and_bullet(env, fred):
    assert _run(env, fred, FakeAnthropic()) == 0

    # BLS revises the last month's level down by 32K; FRED's last_updated moves.
    payems = fred.series["PAYEMS"]
    last_date, last_value = payems[-1]
    fred.overrides["PAYEMS"] = {last_date: last_value - 32}
    fred.last_updated["PAYEMS"] = "2026-10-02 08:30:00-05"
    client = FakeAnthropic()
    assert _run(env, fred, client) == 0

    output = MacroOutput.model_validate(_latest(env))
    payrolls = next(i for i in output.indicators if i.id == "payrolls")
    assert payrolls.revision is not None
    assert (payrolls.revision.old, payrolls.revision.new) == (120, 88)
    assert payrolls.revision.format == "count_signed_thousands"
    assert output.meta.data_changed is True
    assert any(e.type == "revision" for e in output.events)
    assert any("revised" in b.text for b in output.brief.bullets)
    assert len(client.calls) == 1  # just the brief: no new statement or minutes


def test_forced_guard_failure_falls_back_to_template(env, fred):
    def fake_numbers(prompt):
        return {
            "bullets": [{"text": "CPI jumped 9.87% in a month.", "event_ids": [prompt["events"][0]["id"]]}]
        }

    client = FakeAnthropic({"BriefDraft": fake_numbers})
    assert _run(env, fred, client) == 0

    output = MacroOutput.model_validate(_latest(env))
    assert output.brief.narrative_source == "template"
    assert output.brief.model is None
    assert "9.87" not in json.dumps(output.brief.model_dump(mode="json"))
    brief_calls = [c for c in client.calls if c["output_format"] == "BriefDraft"]
    assert len(brief_calls) == 2  # first attempt + one guard retry
    assert "9.87%" in brief_calls[1]["messages"][-1]["content"]  # the retry names the bad number
    failures = [json.loads(x) for x in (env / "data" / "guard_failures.jsonl").read_text().splitlines()]
    assert [f["attempt"] for f in failures] == [1, 2]
    assert all(f["purpose"] == "macro:brief" for f in failures)


def test_refusal_falls_back_to_template_fomc_read(env, fred):
    client = FakeAnthropic()
    original = client.messages.parse

    def parse(*, output_format, **params):
        response = original(output_format=output_format, **params)
        if output_format.__name__ == "FomcReadDraft":
            response.stop_reason = "refusal"
            response.stop_details = None
        return response

    client.messages.parse = parse
    assert _run(env, fred, client) == 0
    read = MacroOutput.model_validate(_latest(env)).fomc.latest.read
    assert read.narrative_source == "template"
    assert read.tone_shift == "more_hawkish"  # from the hike itself
    assert read.cited_change_idx == [0]  # the decision sentence


def test_dry_run_calls_no_llm_and_publishes_nothing(env, fred):
    client = FakeAnthropic()
    assert _run(env, fred, client, dry_run=True) == 0
    assert client.calls == []
    assert not (env / "public-data").exists()
    assert not state_path().exists()


def test_cost_cap_fails_run_and_keeps_previous_latest(env, fred, monkeypatch):
    assert _run(env, fred, FakeAnthropic()) == 0
    before = (env / "public-data" / "latest.json").read_bytes()

    fred.last_updated["CPIAUCSL"] = "2026-10-14 08:30:00-05"
    fred.series["CPIAUCSL"].append((fred.series["CPIAUCSL"][-1][0] + timedelta(days=31), 999.0))
    monkeypatch.setenv("AGENTS_CORE_MAX_RUN_USD", "0.0001")
    assert _run(env, fred, FakeAnthropic()) == 1

    assert (env / "public-data" / "latest.json").read_bytes() == before
    manifest = json.loads((env / "public-data" / "manifest-entry.json").read_text())
    assert manifest["status"] == "failed"


def test_rejected_fred_key_fails_fast_with_an_alert(env, fred, monkeypatch, caplog):
    alerts = []
    monkeypatch.setattr(
        "agents_core.agent.RunContext.alert", lambda self, title, body, **kw: alerts.append((title, body))
    )
    fred.reject_key = True
    assert _run(env, fred, FakeAnthropic()) == 1
    # stops after KEY_REJECTED_AFTER series instead of trying all 25
    assert len(fred.requests) == 3
    [(title, body)] = alerts
    assert title == "macro: FRED rejected FRED_API_KEY"
    assert "FRED_API_KEY is invalid" in body and "CPIAUCSL" in body
    assert "FRED_API_KEY is invalid" in caplog.text


def test_a_single_bad_series_is_not_a_key_problem(env, fred, monkeypatch):
    alerts = []
    monkeypatch.setattr(
        "agents_core.agent.RunContext.alert", lambda self, title, body, **kw: alerts.append(title)
    )
    handle = fred.handle

    def first_three_400(request):
        sid = request.url.params.get("series_id")
        if sid in ("CPILFESL", "PCEPI", "PCEPILFE"):
            return httpx.Response(400, json={"error_code": 400})
        return handle(request)

    monkeypatch.setattr(fred, "handle", first_three_400)
    assert _run(env, fred, FakeAnthropic()) == 0  # CPIAUCSL succeeded first: key is fine
    assert "macro: FRED rejected FRED_API_KEY" not in alerts
    assert any("CPILFESL" in w for w in _latest(env)["meta"]["warnings"])


def test_rerun_republishes_from_previous_latest_not_state(env, fred):
    """Since agents-core v0.2.0 the data branch is restored into public-data/, so the
    FOMC block, minutes and headline come from the previous latest.json; state.json
    no longer carries them."""
    assert _run(env, fred, FakeAnthropic()) == 0
    state = json.loads(state_path().read_text())
    assert set(state["fomc"]) == {"latest_statement_date", "latest_minutes_date"}
    assert "headline" not in state["last_brief"] and "releases" not in state

    assert _run(env, fred, FakeAnthropic()) == 0
    assert _latest(env)["fomc"]["latest"]["read"]["narrative_source"] == "llm"


def test_investigator_guard_failure_retries_then_ships_template(env, fred):
    def invents(params):
        return good_investigator(params, invent_number=True)

    client = FakeAnthropic(investigator=invents)
    assert _run(env, fred, client) == 0

    inv = MacroOutput.model_validate(_latest(env)).investigation
    assert inv.narrative_source == "template" and inv.model is None
    assert "987.6" not in inv.analysis
    assert inv.analysis.startswith("The FOMC's decision was a hike")
    assert inv.loop.stop_reason == "finished" and inv.loop.guard_attempts == 2
    loop_calls = [c for c in client.calls if c["output_format"] == "investigator"]
    assert len(loop_calls) == 3  # tools, finish, retried finish
    failures = [json.loads(x) for x in (env / "data" / "guard_failures.jsonl").read_text().splitlines()]
    assert [f["purpose"] for f in failures] == ["macro:investigator:finish"] * 2


def test_investigator_budget_stop_is_a_warning_not_a_failure(env, fred):
    def never_finishes(params):
        n = len(params["messages"])
        return [{"type": "tool_use", "id": f"t{n}", "name": "get_fomc_context", "input": {}}]

    client = FakeAnthropic(investigator=never_finishes)
    assert _run(env, fred, client) == 0

    output = MacroOutput.model_validate(_latest(env))
    assert output.meta.status == "ok"
    assert output.investigation.narrative_source == "template"
    assert output.investigation.loop.stop_reason in ("max_steps", "max_usd")
    assert output.investigation.loop.steps <= 8
    assert any("release investigator stopped" in w for w in output.meta.warnings)


def test_no_anthropic_key_publishes_templates_with_a_warning(env, fred, monkeypatch):
    """agents-hub: a missing key must degrade to template output, not fail the run."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AGENTS_ANTHROPIC_API_KEY", raising=False)
    assert _run(env, fred, None) == 0

    output = MacroOutput.model_validate(_latest(env))
    assert output.meta.status == "ok"
    assert output.meta.cost_usd == 0
    assert output.meta.warnings == ["No Anthropic API key configured: published template narrative only"]
    assert output.brief.narrative_source == "template" and output.brief.bullets
    assert output.fomc.latest.read.narrative_source == "template"
    assert output.fomc.minutes.narrative_source == "template"
    assert output.investigation.narrative_source == "template" and output.investigation.loop is None
    manifest = json.loads((env / "public-data" / "manifest-entry.json").read_text())
    assert manifest["status"] == "ok"


def test_failed_series_is_stale_with_status_ok_and_a_warning(env, fred):
    """§10: FRED failing for one series publishes the rest, `status: ok` + a warning."""
    assert _run(env, fred, FakeAnthropic()) == 0
    original = fred.handle

    def flaky(request):
        if request.url.params.get("series_id") == "UMCSENT":
            return httpx.Response(500)
        return original(request)

    fred.handle = flaky
    assert _run(env, fred, FakeAnthropic()) == 0
    output = MacroOutput.model_validate(_latest(env))
    assert output.meta.status == "ok"
    assert next(i for i in output.indicators if i.id == "umich_sentiment").stale is True
    assert any("UMCSENT" in w for w in output.meta.warnings)


def test_lost_previous_output_rediscovers_the_latest_statement(env, fred):
    """No previous latest.json and no legacy blocks in state: the latest statement and
    minutes are rebuilt from the feed instead of publishing an empty FOMC block."""
    assert _run(env, fred, FakeAnthropic()) == 0
    (env / "public-data" / "latest.json").unlink()
    assert _run(env, fred, FakeAnthropic()) == 0
    output = MacroOutput.model_validate(_latest(env))
    assert output.fomc.latest.date == date(2026, 9, 16) and output.fomc.latest.read is not None
    assert output.fomc.minutes.summary
