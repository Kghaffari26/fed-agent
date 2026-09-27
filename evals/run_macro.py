"""Run every macro eval suite (evals/macro/suites.py) under ONE total spend cap.

    uv run python evals/run_macro.py                  # all suites, real LLM calls
    uv run python evals/run_macro.py --no-llm         # the deterministic suites only
    uv run python evals/run_macro.py --max-usd 0.60   # total cap (default: $AGENTS_CORE_EVAL_MAX_USD or 0.60)
    uv run python evals/run_macro.py --suite macro-investigator

`agents-evals run` caps each suite separately; this runs them in order and gives each
what's left of the total, so the PR workflow's `max_usd` bounds the whole run. Each
suite appends its line to evals/history.jsonl and its report to
evals/results/<date>.json (agents_core.evals.run_suite); `agents-evals compare` then
diffs the latest entries against the previous ones.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Eval guard failures stay out of the agent's data/guard_failures.jsonl (run state).
os.environ.setdefault(
    "AGENTS_CORE_GUARD_FAILURES_PATH",
    str(Path(__file__).resolve().parent / "results" / "guard_failures.jsonl"),
)

from agents_core import settings  # noqa: E402
from agents_core.evals import run_suite  # noqa: E402

from evals.macro.suites import ALL, TEMPLATES  # noqa: E402

DEFAULT_TOTAL_USD = 0.60


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--no-llm", action="store_true", help="only the deterministic suites")
    parser.add_argument("--max-usd", type=float, default=None, help="total cap across all suites")
    parser.add_argument("--suite", action="append", help="run only these suite names")
    parser.add_argument("--no-write", action="store_true", help="don't write results/history")
    args = parser.parse_args(argv)
    settings.load_dotenv()

    total = args.max_usd
    if total is None:
        total = float(os.environ.get("AGENTS_CORE_EVAL_MAX_USD") or DEFAULT_TOTAL_USD)
    suites = [TEMPLATES] if args.no_llm else ALL
    if args.suite:
        suites = [s for s in suites if s.name in args.suite]

    spent = 0.0
    for suite in suites:
        remaining = max(total - spent, 0.0)
        report = run_suite(suite, max_usd=remaining, write=not args.no_write)
        spent += report.usd
        scores = " ".join(f"{k}={v:.2f}" for k, v in report.scores.items())
        print(
            f"{report.suite}: pass_rate={report.pass_rate:.2f} {scores} usd={report.usd:.4f}"
            + (" (spend cap reached)" if report.budget_exhausted else "")
        )
    print(f"total eval spend: ${spent:.4f} (cap ${total:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
