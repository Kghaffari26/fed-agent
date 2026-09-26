# macro agent — eval summary

Full results: `evals/results/macro-2026-09-26.json`. Run with `uv run python evals/run_macro.py`
(real LLM calls through `agents_core.llm`, capped at $0.20; `--no-llm` for the template checks only).
This run: 12 calls, **$0.0976**.

| Eval (§11) | Fixture | Result | Pass criteria |
|---|---|---|---|
| Number fidelity | 5 scenario fixtures (CPI day, jobs day with big revisions, FOMC day, quiet day, delayed-release day) | **100%** of final bullets pass the guard (re-checked independently); **100%** first-attempt pass | 100% final, ≥ 90% first attempt — **pass** |
| Event grounding | same, LLM bullets | **100%** (every `event_id` exists; top-priority event covered) | **pass** |
| Style | same, LLM bullets | **100%** (no banned words/advice, ≤ 30 words) | **pass** |
| FOMC tone | 6 real statement pairs (2022-2026) saved from federalreserve.gov | **83%** (5/6) match; near-identical pair → `unchanged` | ≥ 80% and identical pair unchanged — **pass**, against **PROVISIONAL** labels |
| Phrase verbatim | same 6 pairs | **100%** raw and after code filtering | 100% — **pass** |
| Template fallback (style + grounding) | 5 scenario fixtures | 100% / 100% | sanity check of the guard fallback |

## FOMC tone labels are PROVISIONAL

`evals/macro/labels_proposed.json` holds my labels, re-derived this session from the real statement text
and the code-computed diffs (the previous session's were from memory, with no network access). Two changed
as a result, and a human should confirm all six before the eval is treated as final:

| Pair | Decision | Label | Model | Note |
|---|---|---|---|---|
| 2024-07-31 → 2024-09-18 | cut 50 bp | more_dovish | more_dovish | |
| 2022-03-16 → 2022-05-04 | hike 50 bp | more_hawkish | more_hawkish | |
| 2026-07-29 → 2026-09-16 | hike 25 bp | more_hawkish | more_hawkish | |
| 2024-06-12 → 2024-07-31 | hold | more_dovish | more_dovish | was "unchanged" (memory); the real diff has 5 softening edits |
| 2023-12-13 → 2024-01-31 | hold | more_dovish | **more_hawkish** | was "more_hawkish" (memory). **Low confidence**: the text drops the tightening bias (dovish) but adds "does not expect it will be appropriate to reduce the target range until…" (hawkish guardrail). The model's miss is defensible. |
| 2026-06-17 → 2026-07-29 | hold | unchanged | unchanged | near-identical pair (one edit: "reaffirmed" → "is continuing") |

One guard retry happened during the evals: the FOMC read for the 2022 pair wrote "25 basis point" (it
converted "1/4" itself); the guard rejected it, the retry passed. That's the guard doing its job.
