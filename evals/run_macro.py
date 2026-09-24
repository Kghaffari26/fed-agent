"""Run the macro agent's evals (§11).

Usage:
    uv run python evals/run_macro.py

What this can actually run right now:
- "Style" and "Event grounding" against the deterministic TEMPLATE brief for
  each fixture in evals/macro/fixtures/ — no LLM call needed, so these run
  for real and are not provisional.
- "FOMC tone" is written up as proposed labels in
  evals/macro/labels_proposed.json (PROVISIONAL — see that file).

What's blocked until agents-core is installable (see STATUS.md):
- "Number fidelity" against a REAL LLM-generated brief (the template brief
  trivially passes the guard by construction, so running the check against
  it wouldn't test anything).
- "Phrase verbatim" against a REAL FOMC-read LLM call.
- Confirming the FOMC tone labels against a REAL model call.

Results are written to evals/results/macro-<date>.json, with each section's
`status` set to "ran" or "blocked" so the summary is honest about what was
actually exercised.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.macro.events import Event  # noqa: E402
from agents.macro.templates import template_brief  # noqa: E402
from evals.macro.event_grounding import check_event_grounding  # noqa: E402
from evals.macro.style_check import check_style  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "macro" / "fixtures"
RESULTS_DIR = Path(__file__).parent / "results"
LABELS_PATH = Path(__file__).parent / "macro" / "labels_proposed.json"


def _event_from_fixture(raw: dict) -> Event:
    return Event(id=raw["id"], type=raw["type"], priority=raw["priority"], facts=raw["facts"])


def run_style_and_grounding() -> dict:
    per_fixture = []
    for fixture_path in sorted(FIXTURES_DIR.glob("*.json")):
        raw = json.loads(fixture_path.read_text())
        events = [_event_from_fixture(e) for e in raw["events"]]
        bullets_text = template_brief(events)
        style = check_style(bullets_text)

        bullet_dicts = [
            {"text": t, "event_ids": [e.id]} for t, e in zip(bullets_text, events, strict=True)
        ]
        grounding = check_event_grounding(
            bullet_dicts,
            valid_event_ids={e.id for e in events},
            top_priority_event_id=raw.get("top_priority_event_id"),
        )
        per_fixture.append(
            {
                "scenario": raw["scenario"],
                "style_ok": style.ok,
                "style_violations": [v.__dict__ for v in style.violations],
                "grounding_ok": grounding.ok,
                "grounding_unknown_event_ids": grounding.unknown_event_ids,
                "grounding_top_priority_covered": grounding.top_priority_covered,
            }
        )
    all_style_ok = all(f["style_ok"] for f in per_fixture)
    all_grounding_ok = all(f["grounding_ok"] for f in per_fixture)
    return {
        "status": "ran",
        "note": "Checks run against the deterministic TEMPLATE brief (no LLM call).",
        "fixtures": per_fixture,
        "style_pass_rate": sum(f["style_ok"] for f in per_fixture) / len(per_fixture),
        "grounding_pass_rate": sum(f["grounding_ok"] for f in per_fixture) / len(per_fixture),
        "all_style_ok": all_style_ok,
        "all_grounding_ok": all_grounding_ok,
    }


def load_fomc_tone_labels() -> dict:
    raw = json.loads(LABELS_PATH.read_text())
    return {
        "status": raw["status"],
        "note": raw["note"],
        "pair_count": len(raw["pairs"]),
        "pairs": raw["pairs"],
    }


def main() -> int:
    results = {
        "agent": "macro",
        "run_at": datetime.now(UTC).isoformat(),
        "number_fidelity": {
            "status": "blocked",
            "reason": "needs a real LLM call through agents_core.llm, not installable yet (see STATUS.md)",
        },
        "event_grounding_and_style": run_style_and_grounding(),
        "fomc_tone": load_fomc_tone_labels(),
        "phrase_verbatim": {
            "status": "blocked",
            "reason": "needs a real FOMC-read LLM call through agents_core.llm, not installable yet",
        },
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"macro-{date.today().isoformat()}.json"
    out_path.write_text(json.dumps(results, indent=2) + "\n")
    print(f"wrote {out_path}")
    print(
        f"style pass rate: {results['event_grounding_and_style']['style_pass_rate']:.0%}, "
        f"grounding pass rate: {results['event_grounding_and_style']['grounding_pass_rate']:.0%}"
    )
    print(f"FOMC tone labels proposed for {results['fomc_tone']['pair_count']} pairs (PROVISIONAL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
