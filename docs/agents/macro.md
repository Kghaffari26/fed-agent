# macro agent — eval summary

Full results: `evals/results/macro-2026-09-24.json`. Run with `uv run python evals/run_macro.py`.

| Eval | Status | Result |
|---|---|---|
| Number fidelity | **Blocked** | Needs a real LLM call through `agents_core.llm` (not installable yet — see STATUS.md). |
| Event grounding | Ran | 100% pass (5/5 fixtures) against the deterministic template brief. |
| Style | Ran | 100% pass (5/5 fixtures) against the deterministic template brief. |
| FOMC tone | **Provisional** | 5 real historical meeting pairs labeled from training-data knowledge (no live fetch possible); see `evals/macro/labels_proposed.json`. Not yet confirmed against real statement text or a real model call. |
| Phrase verbatim | **Blocked** | Needs a real FOMC-read LLM call through `agents_core.llm`. |

Event grounding and style ran for real (not provisional) because they check a deterministically-rendered
brief — no LLM involved — so there was nothing blocking them. Number fidelity and phrase verbatim are
meaningless to run against template output (it trivially satisfies the guard by construction), so they're
marked blocked rather than faked.

**To finish this eval suite**: once `agents-core` is installable and network access allows reaching
`api.stlouisfed.org` / `www.federalreserve.gov` (see STATUS.md), re-run `evals/run_macro.py` after wiring
real LLM calls through `agents_core.llm`, and separately confirm or correct each `labels_proposed.json`
entry against the real statement text before treating it as final.
