"""The macro agent's evals (SPEC_MACRO.md §11) as `agents_core.evals` suites.

    uv run agents-evals run evals.macro.suites:TEMPLATES evals.macro.suites:BRIEF \
        evals.macro.suites:FOMC_READ evals.macro.suites:INVESTIGATOR --total-max-usd 0.60
    uv run agents-evals run evals.macro.suites:BRIEF                # one suite (its own cap)
    uv run agents-evals compare --threshold 0.05                    # vs the previous history entry

Every run appends one line per suite to evals/history.jsonl (prompt version, git SHA,
model, scores, pass rate, cost) and writes evals/results/<date>.json.

Suites:
- TEMPLATES     the deterministic template brief (the guard's fallback) on the 5
                scenario fixtures: style and event grounding. No LLM.
- BRIEF         the real what-changed brief (`analyze.generate_brief`, the production
                path) on the 5 scenario fixtures: number fidelity (every final bullet
                re-checked with the number guard), first-attempt guard pass, style, grounding.
- FOMC_READ     the real FOMC read on real statement pairs from federalreserve.gov
                against evals/macro/labels_proposed.json (PROVISIONAL labels): tone,
                verbatim key phrases, cited change indices.
- INVESTIGATOR  the release investigator (`investigate.investigate`, the production
                agent loop) on the same 5 scenario fixtures, with its tools serving real
                FRED history recorded by scripts/record_eval_series.py. Trajectory
                scorers (required tools, forbidden tools, max steps, stop reason), the
                trigger decision, number support, citations, and an LLM-judge quality
                score. CPI, jobs and FOMC days must run the loop; the quiet and
                delayed-release days must not (a trajectory scorer passes on those only
                if no loop ran).

With AGENTS_CORE_GUARD_FAILURES_PATH=evals/results/guard_failures.jsonl (as evals.yml sets it),
guard failures during evals go there, not the agent's data/guard_failures.jsonl; LLM
spend goes to data/eval_costs.jsonl (agents-core's default;
it never enters the published costs-summary.json).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

EVALS = Path(__file__).resolve().parent.parent

from agents_core.evals import (  # noqa: E402
    EvalCase,
    EvalContext,
    EvalOutput,
    EvalSuite,
    LLMJudge,
    Score,
    Scorer,
    forbidden_tools_not_called,
    max_steps,
    required_tools_called,
    stop_reason,
)
from agents_core.guards import verify_numbers  # noqa: E402

from agents.macro import investigate as investigator  # noqa: E402
from agents.macro.analyze import (  # noqa: E402
    GUARD_ALLOW,
    WhatChangedInput,
    generate_brief,
    generate_fomc_read,
)
from agents.macro.build import StatementInput, build_fomc_latest  # noqa: E402
from agents.macro.config import IndicatorConfig, load_macro_config  # noqa: E402
from agents.macro.events import Event, rank_events  # noqa: E402
from agents.macro.fetch_fred import Observation  # noqa: E402
from agents.macro.fomc import ExtractedStatement, parse_decision, parse_votes  # noqa: E402
from agents.macro.investigate import (  # noqa: E402
    MAX_STEPS,
    InvestigatorData,
    SeriesSource,
    Trigger,
    investigate,
    pick_trigger,
)
from agents.macro.pipeline import compute_indicator_snapshot  # noqa: E402
from agents.macro.templates import template_brief  # noqa: E402
from evals.macro.event_grounding import check_event_grounding  # noqa: E402
from evals.macro.style_check import check_style  # noqa: E402

FIXTURES = EVALS / "macro" / "fixtures"
FOMC_FIXTURES = FIXTURES / "fomc"
SERIES_FIXTURE = FIXTURES / "investigator" / "series.json"
LABELS = EVALS / "macro" / "labels_proposed.json"

# Bump when a prompt in agents/macro/analyze.py changes (history keys comparisons on it).
BRIEF_PROMPT_VERSION = "brief-2026-09-26"
FOMC_PROMPT_VERSION = "fomc-read-2026-09-26"


class Check(Scorer):
    """A named scorer from a function returning (value, passed, detail)."""

    def __init__(self, name: str, fn: Callable[[EvalCase, EvalOutput], tuple[float, bool, str]]) -> None:
        self.name = name
        self.fn = fn

    def score(self, case: EvalCase, out: EvalOutput, ctx: EvalContext) -> Score:
        value, passed, detail = self.fn(case, out)
        return self._result(value, passed, detail)


def _passfail(ok: bool, detail: str = "") -> tuple[float, bool, str]:
    return (1.0 if ok else 0.0, ok, detail)


# ---- scenario fixtures --------------------------------------------------------------


def scenario_cases() -> list[EvalCase]:
    cases = []
    for path in sorted(FIXTURES.glob("*.json")):
        raw = json.loads(path.read_text())
        cases.append(EvalCase(id=path.stem, input=raw, expected={"top": raw.get("top_priority_event_id")}))
    return cases


def _events(raw: dict) -> list[Event]:
    return [
        Event(id=e["id"], type=e["type"], priority=e["priority"], facts=e["facts"]) for e in raw["events"]
    ]


def _ranked(raw: dict) -> list[Event]:
    return rank_events(_events(raw))


def _grounding(bullets: list[dict], raw: dict):
    return check_event_grounding(
        bullets,
        valid_event_ids={e["id"] for e in raw["events"]},
        top_priority_event_id=raw.get("top_priority_event_id"),
    )


# ---- TEMPLATES ------------------------------------------------------------------------


def run_template(case: EvalCase, ectx: EvalContext) -> dict:
    events = _events(case.input)
    texts = template_brief(events)
    return {"bullets": [{"text": t, "event_ids": [e.id]} for t, e in zip(texts, events, strict=True)]}


def _style(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    result = check_style([b["text"] for b in out.output["bullets"]])
    return _passfail(result.ok, "; ".join(f"{v.bullet_index}:{v.kind}" for v in result.violations))


def _grounded(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    result = _grounding(out.output["bullets"], case.input)
    return _passfail(result.ok, f"unknown={result.unknown_event_ids} top={result.top_priority_covered}")


TEMPLATES = EvalSuite(
    name="macro-templates",
    prompt_version="templates",
    cases=scenario_cases(),
    task=run_template,
    scorers=[Check("style", _style), Check("grounding", _grounded)],
    model="none",
)


# ---- BRIEF ----------------------------------------------------------------------------


def run_brief(case: EvalCase, ectx: EvalContext) -> dict:
    config = load_macro_config()
    raw = case.input
    events = _events(raw)
    result = generate_brief(
        ectx.llm,
        WhatChangedInput(as_of=raw["as_of"], events=events, context=raw["context"]),
        indicator_source_urls={i.id: i.source_url for i in config.indicators},
        indicator_names={i.id: i.name for i in config.indicators},
    )
    return {
        "bullets": [{"text": b.text, "event_ids": b.event_ids} for b in result.bullets],
        "narrative_source": result.narrative_source,
        "attempts": result.attempts,
        "facts": {"events": [e.facts for e in events], "context": raw["context"]},
    }


def _number_fidelity(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    checks = [
        verify_numbers(b["text"], out.output["facts"], allow=GUARD_ALLOW) for b in out.output["bullets"]
    ]
    bad = sorted({t for c in checks for t in c.unsupported})
    return _passfail(not bad, f"unsupported={bad}" if bad else "")


def _first_attempt(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    o = out.output
    return _passfail(
        o["narrative_source"] == "llm" and o["attempts"] == 1, f"{o['narrative_source']} x{o['attempts']}"
    )


BRIEF = EvalSuite(
    name="macro-brief",
    prompt_version=BRIEF_PROMPT_VERSION,
    cases=scenario_cases(),
    task=run_brief,
    scorers=[
        Check("number_fidelity", _number_fidelity),
        Check("first_attempt_guard_pass", _first_attempt),
        Check("style", _style),
        Check("grounding", _grounded),
    ],
)


# ---- FOMC_READ --------------------------------------------------------------------------


def _statement(day: str, previous_range: dict | None = None) -> StatementInput:
    raw = json.loads((FOMC_FIXTURES / f"statement_{day}.json").read_text())
    extracted = ExtractedStatement(policy_text=raw["policy_text"], voting_text=raw["voting_text"])
    return StatementInput(
        date=date.fromisoformat(day),
        url=raw["url"],
        extracted=extracted,
        decision=parse_decision(extracted.policy_text, previous_range),
        votes=parse_votes(extracted.voting_text),
    )


def fomc_cases() -> list[EvalCase]:
    labels = json.loads(LABELS.read_text())
    return [
        EvalCase(
            id=f"{p['previous_date']}_{p['new_date']}",
            input={"previous_date": p["previous_date"], "new_date": p["new_date"]},
            expected={"tone_shift": p["label"], "near_identical": p.get("near_identical", False)},
            metadata={"labels_status": labels["status"], "confidence": p.get("confidence")},
        )
        for p in labels["pairs"]
    ]


def run_fomc_read(case: EvalCase, ectx: EvalContext) -> dict:
    previous = _statement(case.input["previous_date"])
    latest = _statement(case.input["new_date"], previous.decision.target_range)
    block = build_fomc_latest(latest, previous)
    result = generate_fomc_read(ectx.llm, block)
    read = result.read
    return {
        "tone_shift": read.tone_shift,
        "narrative_source": read.narrative_source,
        "cited_change_idx": read.cited_change_idx,
        "valid_idx": [c.idx for c in block.changes],
        "phrases_verbatim": all(kp.phrase in block.latest_text for kp in read.key_phrases),
        "raw_phrase_pass_rate": result.raw_phrase_pass_rate,
        "summary": read.summary,
    }


def _tone(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    got, want = out.output["tone_shift"], case.expected["tone_shift"]
    return _passfail(got == want, f"predicted {got}, label {want}")


def _phrases(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    rate = out.output["raw_phrase_pass_rate"]
    return (rate, out.output["phrases_verbatim"], f"raw verbatim rate {rate:.2f}")


def _cited(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    o = out.output
    return _passfail(set(o["cited_change_idx"]) <= set(o["valid_idx"]), str(o["cited_change_idx"]))


FOMC_READ = EvalSuite(
    name="macro-fomc-read",
    prompt_version=FOMC_PROMPT_VERSION,
    cases=fomc_cases(),
    task=run_fomc_read,
    scorers=[
        Check("tone_match", _tone),
        Check("phrases_verbatim", _phrases),
        Check("cited_idx_valid", _cited),
    ],
    metadata={"labels": "PROVISIONAL"},
)


# ---- INVESTIGATOR ------------------------------------------------------------------------

# Tools that don't exist: calling one means the model invented a tool.
HALLUCINATED_TOOLS = ["fetch_url", "web_search", "search", "get_fred_series", "get_news", "calculator"]
# The tool each triggering scenario must use to answer "what's driving this".
REQUIRED_TOOLS: dict[str | None, list[str]] = {
    "cpi": ["get_components"],
    "payrolls": ["get_components"],
    "unrate": ["get_components"],
    None: ["get_fomc_context"],  # an FOMC decision
}


def _recorded_series() -> dict[str, list[Observation]]:
    raw = json.loads(SERIES_FIXTURE.read_text())["series"]
    return {
        sid: [
            Observation(date=date.fromisoformat(d), value=v)
            for d, v in zip(s["dates"], s["values"], strict=True)
        ]
        for sid, s in raw.items()
    }


def investigator_data(as_of: date) -> InvestigatorData:
    """Real FRED history (recorded) up to `as_of`, shaped as the agent's transform shapes it."""
    config = load_macro_config()
    recorded = _recorded_series()

    def source(cfg: IndicatorConfig, kind: str) -> SeriesSource | None:
        obs = [o for o in recorded.get(cfg.fred_series, []) if o.date <= as_of]
        return SeriesSource(cfg, obs, kind=kind) if obs else None

    series: dict[str, SeriesSource] = {}
    for c in config.components:
        if (src := source(c, "component")) is not None:
            series[c.id] = src
    for i in config.indicators:
        if (src := source(i, "indicator")) is not None:
            series[i.id] = src
    return InvestigatorData(
        today=as_of,
        series=series,
        components={
            r: [c.id for c in config.components_for(r)] for r in {c.release for c in config.components}
        },
    )


def scenario_trigger(raw: dict, data: InvestigatorData) -> Trigger | None:
    """The scenario's trigger, with a new release's facts recomputed from the recorded
    data (the production path), so the task and the tools agree on every number."""
    trigger = pick_trigger(_ranked(raw))
    if trigger is None or trigger.type != "new_release":
        return trigger
    src = data.series[trigger.indicator_id]
    snapshot = compute_indicator_snapshot(src.config, src.observations, {}, updated=True)
    event = next(e for e in snapshot.events if e.type == "new_release")
    return Trigger(event_id=event.id, type=event.type, indicator_id=trigger.indicator_id, facts=event.facts)


def scenario_fomc_context(raw: dict) -> dict[str, Any]:
    """get_fomc_context for a scenario: its FOMC decision facts, or the context's range."""
    decision = next((e for e in raw["events"] if e["type"] == "fomc_decision"), None)
    ctx: dict[str, Any] = {
        "target_range": raw["context"].get("fed_target_range"),
        "policy_regime": raw["context"].get("regimes", {}).get("policy"),
    }
    if decision:
        f = decision["facts"]
        ctx.update(
            date=raw["as_of"],
            decision=f["decision"],
            target_range=f["target_range"],
            change_bp=f["change_bp"],
            dissents=f["dissents"],
        )
    return ctx


def investigator_cases() -> list[EvalCase]:
    """One case per scenario fixture. The case input is what the loop actually sees
    (the trigger, its facts recomputed from the recorded FRED data, the FOMC context),
    so the LLM judge grades the analysis against the same numbers the agent had,
    not the scenario's hypothetical ones."""
    cases = []
    for path in sorted(FIXTURES.glob("*.json")):
        raw = json.loads(path.read_text())
        data = investigator_data(date.fromisoformat(raw["as_of"]))
        trigger = scenario_trigger(raw, data)
        fomc = scenario_fomc_context(raw)
        cases.append(
            EvalCase(
                id=path.stem,
                input={
                    "scenario": raw["scenario"],
                    "as_of": raw["as_of"],
                    "events": [e.id for e in _ranked(raw)],
                    "trigger": None
                    if trigger is None
                    else {
                        "event_id": trigger.event_id,
                        "type": trigger.type,
                        "series_id": trigger.indicator_id,
                        "facts": trigger.facts,
                    },
                    "fomc_context": fomc,
                },
                expected={
                    "runs": trigger is not None,
                    "trigger_type": trigger.type if trigger else None,
                    "required_tools": REQUIRED_TOOLS.get(trigger.indicator_id, []) if trigger else [],
                },
                tags=["triggers" if trigger else "no-trigger"],
            )
        )
    return cases


def run_investigator(case: EvalCase, ectx: EvalContext) -> EvalOutput:
    spec = case.input
    if spec["trigger"] is None:
        return EvalOutput({"ran": False, "trigger": None})
    data = investigator_data(date.fromisoformat(spec["as_of"]))
    data.fomc = spec["fomc_context"]
    t = spec["trigger"]
    trigger = Trigger(event_id=t["event_id"], type=t["type"], indicator_id=t["series_id"], facts=t["facts"])
    result = investigate(ectx.llm, data, trigger)
    if result.loop is not None and os.environ.get("MACRO_SAVE_TRAJECTORIES"):
        # Recorded real trajectories replay offline in tests (agents_core ReplayClient).
        result.loop.trajectory.save(
            Path(os.environ["MACRO_SAVE_TRAJECTORIES"]) / f"{case.id}.trajectory.json"
        )
    output = {
        "ran": True,
        "trigger": trigger.event_id,
        "analysis": result.draft.analysis,
        "words": len(result.draft.analysis.split()),
        "cited_series": result.cited_series,
        "narrative_source": result.narrative_source,
        "tool_calls": result.loop.tools_called() if result.loop else [],
        "numbers_ok": verify_numbers(result.draft.analysis, result.facts, allow=investigator.GUARD_ALLOW).ok,
        "series_seen": sorted(result.series_seen),
    }
    return EvalOutput(output, loop=result.loop)


class WhenTriggered(Scorer):
    """Runs `inner` on cases that must run the loop; on the quiet and delayed-release
    days it instead checks that no loop ran (1 if none did, else 0)."""

    def __init__(self, inner: Scorer) -> None:
        self.inner = inner
        self.name = inner.name

    def score(self, case: EvalCase, out: EvalOutput, ctx: EvalContext) -> Score:
        if case.expected["runs"]:
            return self.inner.score(case, out, ctx)
        ran = out.loop is not None
        return self._result(
            0.0 if ran else 1.0, not ran, "loop ran on a no-trigger day" if ran else "n/a: no loop"
        )


class RequiredTools(Scorer):
    name = "required_tools_called"

    def score(self, case: EvalCase, out: EvalOutput, ctx: EvalContext) -> Score:
        return required_tools_called(case.expected["required_tools"]).score(case, out, ctx)


def _trigger_ok(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    ran = out.output["ran"]
    return _passfail(ran == case.expected["runs"], f"ran={ran} expected={case.expected['runs']}")


def _numbers(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    if not case.expected["runs"]:
        return _passfail(not out.output["ran"], "n/a")
    o = out.output
    return _passfail(o["narrative_source"] == "llm" and o["numbers_ok"], f"{o['narrative_source']}")


def _citations(case: EvalCase, out: EvalOutput) -> tuple[float, bool, str]:
    if not case.expected["runs"]:
        return _passfail(not out.output["ran"], "n/a")
    cited = out.output["cited_series"]
    return _passfail(bool(cited) and set(cited) <= set(out.output["series_seen"]), str(cited))


JUDGE_RUBRIC = """\
The input is the release (or FOMC decision) that triggered an investigation: its facts \
and the FOMC context. The output is the agent's short "what's driving this" analysis; \
the agent also looked data up with the tools in `tool_calls` (components, history, \
percentiles, the 2019 and 2022-23 cycles), so numbers beyond the input come from those \
tools and were verified separately against the tool outputs — don't penalize a number \
just because it isn't in the input, but do penalize one that contradicts the input. Score 1-5:
5 = explains what drove the release (the components or sectors behind it, or for an FOMC \
decision the data it sits next to), compares with history where useful, is specific and \
neutral, makes no forecasts, gives no advice, doesn't speculate about the Fed's motives, \
and is at most 120 words (`words`);
3 = accurate but generic (mostly restates the headline number), misses the obvious \
driver, speculates about motives, or runs long;
1 = contradicts the input, is speculative or promotional, or is off-topic."""


INVESTIGATOR = EvalSuite(
    name="macro-investigator",
    prompt_version=investigator.PROMPT_VERSION,
    cases=investigator_cases(),
    task=run_investigator,
    scorers=[
        Check("trigger_correct", _trigger_ok),
        WhenTriggered(RequiredTools()),
        WhenTriggered(forbidden_tools_not_called(HALLUCINATED_TOOLS)),
        WhenTriggered(max_steps(MAX_STEPS - 2)),  # a focused investigation leaves budget unused
        WhenTriggered(stop_reason("finished")),
        Check("numbers_supported", _numbers),
        Check("citations_valid", _citations),
        WhenTriggered(
            LLMJudge(
                JUDGE_RUBRIC,
                tier="fast",
                output=lambda o: {k: o[k] for k in ("analysis", "words", "cited_series", "tool_calls")},
                name="judge_quality",
            )
        ),
    ],
)

ALL = [TEMPLATES, BRIEF, FOMC_READ, INVESTIGATOR]
