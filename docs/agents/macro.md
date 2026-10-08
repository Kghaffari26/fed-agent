# macro agent: eval summary

The evals are `agents_core.evals` suites in `evals/macro/suites.py`. Run them with
`uv run agents-evals run evals.macro.suites:TEMPLATES evals.macro.suites:BRIEF evals.macro.suites:FOMC_READ
evals.macro.suites:INVESTIGATOR --total-max-usd 0.60`: real LLM calls under one total cap of $0.60 (agents-core
v0.3.1). `TEMPLATES` alone is the deterministic, no-LLM suite.

- Each run appends one line per suite to `evals/history.jsonl` and writes `evals/results/<date>.json`.
- `uv run agents-evals compare` shows the score deltas against the previous entry.
- On pull requests, `.github/workflows/evals.yml` runs agents-core's `run-evals.yml` (v0.3.2, pinned by SHA). It fails the PR when a score
  drops by more than 0.10.

Latest run: 2026-09-27, `evals/results/2026-09-27.json`, $0.14 for all four suites.

| Suite | Cases | Scorers → result |
|---|---|---|
| `macro-investigator` | 5 scenario fixtures (CPI day, jobs day with big revisions, FOMC day, quiet day, delayed-release day). Tools serve real FRED history recorded 2026-09-27. | trigger_correct 1.00 · required_tools_called 1.00 · forbidden_tools_not_called 1.00 · max_steps (≤ 6) 1.00 · stop_reason `finished` 1.00 · numbers_supported 1.00 · citations_valid 1.00 · **judge_quality 1.00** · pass rate **1.00** ($0.041) |
| `macro-brief` | same 5 fixtures | number_fidelity 1.00 · first_attempt_guard_pass 1.00 · style 1.00 · grounding 1.00 ($0.022) |
| `macro-fomc-read` | 6 real statement pairs (2022-2026) | tone_match **0.83** (5/6, PROVISIONAL labels) · phrases_verbatim 1.00 · cited_idx_valid 1.00 ($0.078) |
| `macro-templates` | 5 fixtures, no LLM | style 1.00 · grounding 1.00 |

## Investigator trajectory evals

- **Required tools.** CPI and jobs days must call `get_components`. The FOMC day must call
  `get_fomc_context`.
- **Forbidden tools.** Names of tools that don't exist (`web_search`, `fetch_url`, `get_fred_series`, …).
  Calling one means the model invented a tool.
- **Quiet and delayed-release days.** These must not start a loop. Each trajectory scorer passes on them only
  if no loop ran.
- **Numbers.** The final `analysis` is re-checked with the number guard against the trigger's facts plus
  every tool output.
- **Judge.** The fast tier scores the analysis 1-5 against a rubric: explains the driver, compares with
  history, stays neutral, makes no forecasts, doesn't speculate about Fed motives, and keeps to ≤ 120 words.
  The score is normalized to 0-1.
- **Trigger facts.** Each case's trigger facts are recomputed from the recorded data. The task reports the
  loop's own task message plus the FOMC context as `EvalOutput(..., input=...)` (agents-core v0.3.2), and the
  judge shows that instead of the case input, so the loop and the judge see the same numbers.

The first run on 2026-09-27 scored `judge_quality` 0.65: the judge flagged speculation about the Fed's motives
and an over-long CPI analysis. The prompt was fixed, and the score went to 1.00. See
[case study 5](../case-studies.md#5-the-release-investigator-speculated-about-the-feds-motives).

The three real trajectories are saved in `tests/fixtures/investigator/` and replayed offline by
`tests/test_investigator.py` with agents-core's `ReplayClient`.

## FOMC tone labels are PROVISIONAL

`evals/macro/labels_proposed.json` holds labels derived from the real statement text and the code-computed
diffs. A human should confirm all six. The model's one miss is the arguable 2023-12-13 → 2024-01-31 pair:

- the label is `more_dovish`, and the model said `more_hawkish`;
- the statement drops the tightening bias, but it adds "does not expect it will be appropriate to reduce the
  target range until…".

It made the same call on 2026-09-26 and 2026-09-27.
