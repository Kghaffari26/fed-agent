"""Run the macro agent's evals (SPEC_MACRO.md §11).

Usage:
    uv run python evals/run_macro.py            # real LLM calls, capped at EVAL_MAX_USD
    uv run python evals/run_macro.py --no-llm   # only the deterministic template checks

Sections:
- template_checks: style + event grounding of the deterministic TEMPLATE brief (the
  guard fallback) for each scenario fixture. No LLM.
- number_fidelity: the real what-changed brief (agents_core.llm through
  agents.macro.analyze.generate_brief, i.e. the production path) for the 5 scenario
  fixtures. Final bullets are re-checked independently with
  agents_core.guards.verify_numbers; the first-attempt pass rate is recorded (§11:
  100% final, >= 90% first attempt). Style and grounding are checked on these LLM
  bullets too.
- fomc_tone + phrase_verbatim: the real FOMC read on real statement pairs saved from
  federalreserve.gov (evals/macro/fixtures/fomc/), against the labels in
  evals/macro/labels_proposed.json (PROVISIONAL until a human confirms them).

LLM spend goes to evals/results/costs.jsonl (not the agent's data/costs.jsonl, so evals
don't show up in the published costs-summary.json), and guard failures to
evals/results/guard_failures.jsonl. Results: evals/results/macro-<date>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EVALS = Path(__file__).parent
RESULTS_DIR = EVALS / "results"
FIXTURES_DIR = EVALS / "macro" / "fixtures"
FOMC_FIXTURES = FIXTURES_DIR / "fomc"
LABELS_PATH = EVALS / "macro" / "labels_proposed.json"
EVAL_MAX_USD = 0.20  # §11: "a cap of about $0.20 per eval run"

# Route agents-core's logs before anything reads settings.
os.environ.setdefault("AGENTS_CORE_GUARD_FAILURES_PATH", str(RESULTS_DIR / "guard_failures.jsonl"))

from agents_core import settings  # noqa: E402
from agents_core.costs import CostTracker  # noqa: E402
from agents_core.guards import verify_numbers  # noqa: E402
from agents_core.llm import LLM  # noqa: E402

from agents.macro.analyze import (  # noqa: E402
    GUARD_ALLOW,
    WhatChangedInput,
    generate_brief,
    generate_fomc_read,
)
from agents.macro.build import StatementInput, build_fomc_latest  # noqa: E402
from agents.macro.config import load_macro_config  # noqa: E402
from agents.macro.events import Event  # noqa: E402
from agents.macro.fomc import ExtractedStatement, parse_decision, parse_votes  # noqa: E402
from agents.macro.templates import template_brief  # noqa: E402
from evals.macro.event_grounding import check_event_grounding  # noqa: E402
from evals.macro.style_check import check_style  # noqa: E402


def _fixtures() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(FIXTURES_DIR.glob("*.json"))]


def _events(raw: dict) -> list[Event]:
    return [
        Event(id=e["id"], type=e["type"], priority=e["priority"], facts=e["facts"]) for e in raw["events"]
    ]


def _grounding(bullets: list[dict], raw: dict):
    return check_event_grounding(
        bullets,
        valid_event_ids={e["id"] for e in raw["events"]},
        top_priority_event_id=raw.get("top_priority_event_id"),
    )


def run_template_checks() -> dict:
    per_fixture = []
    for raw in _fixtures():
        events = _events(raw)
        texts = template_brief(events)
        style = check_style(texts)
        grounding = _grounding(
            [{"text": t, "event_ids": [e.id]} for t, e in zip(texts, events, strict=True)], raw
        )
        per_fixture.append(
            {
                "scenario": raw["scenario"],
                "style_ok": style.ok,
                "style_violations": [v.__dict__ for v in style.violations],
                "grounding_ok": grounding.ok,
            }
        )
    n = len(per_fixture)
    return {
        "status": "ran",
        "note": "Deterministic TEMPLATE brief (the guard fallback). No LLM call.",
        "fixtures": per_fixture,
        "style_pass_rate": sum(f["style_ok"] for f in per_fixture) / n,
        "grounding_pass_rate": sum(f["grounding_ok"] for f in per_fixture) / n,
    }


def run_number_fidelity(llm: LLM) -> dict:
    names = {i.id: i.name for i in load_macro_config().indicators}
    urls = {i.id: i.source_url for i in load_macro_config().indicators}
    per_fixture = []
    for raw in _fixtures():
        events = _events(raw)
        result = generate_brief(
            llm,
            WhatChangedInput(as_of=raw["as_of"], events=events, context=raw["context"]),
            indicator_source_urls=urls,
            indicator_names=names,
        )
        facts = {"events": [e.facts for e in events], "context": raw["context"]}
        texts = [b.text for b in result.bullets]
        guard = [verify_numbers(t, facts, allow=GUARD_ALLOW) for t in texts]
        style = check_style(texts)
        grounding = _grounding([{"text": b.text, "event_ids": b.event_ids} for b in result.bullets], raw)
        per_fixture.append(
            {
                "scenario": raw["scenario"],
                "narrative_source": result.narrative_source,
                "attempts": result.attempts,
                "first_attempt_pass": result.narrative_source == "llm" and result.attempts == 1,
                "final_bullets_pass_guard": all(g.ok for g in guard),
                "unsupported": sorted({t for g in guard for t in g.unsupported}),
                "style_ok": style.ok,
                "style_violations": [v.__dict__ for v in style.violations],
                "grounding_ok": grounding.ok,
                "top_priority_covered": grounding.top_priority_covered,
                "bullets": texts,
            }
        )
    n = len(per_fixture)
    return {
        "status": "ran",
        "final_guard_pass_rate": sum(f["final_bullets_pass_guard"] for f in per_fixture) / n,
        "first_attempt_pass_rate": sum(f["first_attempt_pass"] for f in per_fixture) / n,
        "llm_style_pass_rate": sum(f["style_ok"] for f in per_fixture) / n,
        "llm_grounding_pass_rate": sum(f["grounding_ok"] for f in per_fixture) / n,
        "fixtures": per_fixture,
    }


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


def run_fomc(llm: LLM) -> tuple[dict, dict]:
    labels = json.loads(LABELS_PATH.read_text())
    pairs = []
    for pair in labels["pairs"]:
        previous = _statement(pair["previous_date"])
        latest = _statement(pair["new_date"], previous.decision.target_range)
        block = build_fomc_latest(latest, previous)
        result = generate_fomc_read(llm, block)
        read = result.read
        pairs.append(
            {
                "previous_date": pair["previous_date"],
                "new_date": pair["new_date"],
                "decision": block.decision,
                "change_bp": block.change_bp,
                "changes": len(block.changes),
                "label": pair["label"],
                "predicted": read.tone_shift,
                "match": read.tone_shift == pair["label"],
                "near_identical": pair.get("near_identical", False),
                "narrative_source": read.narrative_source,
                "cited_change_idx": read.cited_change_idx,
                "cited_idx_valid": set(read.cited_change_idx) <= {c.idx for c in block.changes},
                "raw_phrase_pass_rate": result.raw_phrase_pass_rate,
                "final_phrases_verbatim": all(kp.phrase in block.latest_text for kp in read.key_phrases),
                "summary": read.summary,
                "rationale": read.rationale,
            }
        )
    n = len(pairs)
    identical = [p for p in pairs if p["near_identical"]]
    tone = {
        "status": "ran",
        "labels_status": labels["status"],
        "match_rate": sum(p["match"] for p in pairs) / n,
        "near_identical_unchanged": all(p["predicted"] == "unchanged" for p in identical),
        "pass": sum(p["match"] for p in pairs) / n >= 0.8
        and all(p["predicted"] == "unchanged" for p in identical),
        "pairs": pairs,
    }
    phrases = {
        "status": "ran",
        "final_verbatim_rate": sum(p["final_phrases_verbatim"] for p in pairs) / n,
        "raw_verbatim_rate_mean": sum(p["raw_phrase_pass_rate"] for p in pairs) / n,
    }
    return tone, phrases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="only the deterministic template checks")
    args = parser.parse_args(argv)
    settings.load_dotenv()

    started = datetime.now(UTC)
    results: dict = {
        "agent": "macro",
        "run_at": started.isoformat(),
        "template_checks": run_template_checks(),
    }
    if args.no_llm:
        for key in ("number_fidelity", "fomc_tone", "phrase_verbatim"):
            results[key] = {"status": "skipped", "reason": "--no-llm"}
    else:
        tracker = CostTracker(
            agent="macro-evals",
            run_id=f"evals-{started:%Y-%m-%dT%H-%M-%SZ}",
            max_usd=EVAL_MAX_USD,
            path=RESULTS_DIR / "costs.jsonl",
        )
        llm = LLM(tracker)
        results["number_fidelity"] = run_number_fidelity(llm)
        results["fomc_tone"], results["phrase_verbatim"] = run_fomc(llm)
        results["cost_usd"] = round(tracker.total_usd, 4)
        results["llm_calls"] = tracker.calls
        tracker.record_run("ok")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"macro-{started.date().isoformat()}.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out_path}")
    t = results["template_checks"]
    print(f"template brief: style {t['style_pass_rate']:.0%}, grounding {t['grounding_pass_rate']:.0%}")
    if not args.no_llm:
        nf, tone, ph = results["number_fidelity"], results["fomc_tone"], results["phrase_verbatim"]
        print(
            f"number fidelity: final {nf['final_guard_pass_rate']:.0%}, first attempt "
            f"{nf['first_attempt_pass_rate']:.0%}; LLM style {nf['llm_style_pass_rate']:.0%}, "
            f"grounding {nf['llm_grounding_pass_rate']:.0%}"
        )
        print(
            f"FOMC tone ({tone['labels_status']} labels): {tone['match_rate']:.0%} match, "
            f"near-identical unchanged={tone['near_identical_unchanged']}; phrases verbatim "
            f"final {ph['final_verbatim_rate']:.0%}, raw {ph['raw_verbatim_rate_mean']:.0%}"
        )
        print(f"eval LLM spend: ${results['cost_usd']:.4f} over {results['llm_calls']} calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
