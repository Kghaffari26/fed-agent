# fed-agent

An agent that tracks the key U.S. macroeconomic indicators and Federal Reserve communications, and explains
**what changed since the last release** in plain English — grounded entirely in numbers computed in code,
never numbers produced by a model. Built from [`docs/specs/SPEC_MACRO.md`](docs/specs/SPEC_MACRO.md) for
the `fed-agent` slot in a small family of scheduled data agents (see `agents-core` / `agents-hub`).

## What it does

Every weekday morning (and again on FOMC decision afternoons), it:

1. Pulls ~25 FRED series — CPI, core PCE, payrolls, unemployment, GDP, the Treasury curve, mortgage rates,
   the Fed's own target range, and more — plus the FOMC's RSS feed for new statements and minutes.
2. Computes YoY/MoM/3-month-annualized rates, revision checks, five deterministic "regime" labels
   (inflation, labor, growth, policy, curve), and a ranked list of events (new data, revisions, threshold
   crossings, curve inversions, regime changes) — all in plain Python, no model involved.
3. Only when something actually changed, asks an LLM to write a 3-6 bullet brief from those events — and
   verifies every number in that brief against the computed facts before publishing it. A brief that cites
   an unsupported number gets one retry, then falls back to a deterministic template sentence.
4. On a new FOMC statement, diffs it sentence-by-sentence against the previous one, parses the
   hold/cut/hike decision and any dissents, and has the model explain the tone shift — again with every
   quoted phrase checked verbatim against the real statement text.

### Sample output (from the real run on 2026-09-26)

```json
{
  "headline": "The FOMC raised rates to 3.75–4.00%.",
  "regimes": {
    "inflation": {"label": "Steady", "detail": "Core PCE 3.3% YoY; 3.0% 3-mo annualized"},
    "policy": {"label": "Hiking", "detail": "Target range 3.75–4.00%"}
  },
  "brief": {
    "bullets": [{
      "text": "CPI rose 3.4% year-over-year in August 2026, up from a prior 3.3%, with a monthly increase of 0.4%.",
      "event_ids": ["new_release:cpi:2026-08-01"],
      "citations": [{"name": "FRED: cpi", "url": "https://fred.stlouisfed.org/series/CPIAUCSL"}]
    }],
    "narrative_source": "llm"
  }
}
```

See SPEC_MACRO.md §6 for the full shape; `schemas/macro.schema.json` is the exported JSON Schema.

## How it works

```
fetch ──▶ transform ──▶ detect events ──▶ (if events) analyze ──▶ guard ──▶ validate ──▶ publish
  │           │               │                    │                  │
FRED, Fed   pure Python    pure Python        LLM (smart)      number guard
```

- **Nothing the model writes reaches the site unchecked.** Every number in every LLM-written sentence is
  extracted and matched against the facts it was given; a mismatch triggers one retry, then a template
  fallback — never a published number the model invented.
- **Zero LLM calls on a no-change day.** FRED's `last_updated` timestamp is checked before anything else;
  if it hasn't moved, that series isn't even re-fetched, let alone re-analyzed.
- **The narrative is disposable; the numbers aren't.** Transforms, revisions, regimes, and events are all
  deterministic and unit-tested against hand-computed fixtures — the LLM only ever writes prose around
  numbers that were already correct before it saw them.

## Costs

Measured: the first real run (brief + FOMC read + minutes summary, i.e. a worst-case day) cost **$0.048**;
an immediate re-run cost **$0.00** (zero LLM calls). A normal release day is the brief alone, ~$0.008.
Budgeted at $0.30-0.50/month (SPEC_MACRO.md §12). `max_run_usd: 0.25` caps any single run.

## Running it

`agents-core` (the shared framework: HTTP, LLM, number guard, costs, publishing, runner) is a dependency
pinned to `v0.1.0`. This repo registers the agent through the `agents_core.agents` entry point, so:

```bash
cp .env.example .env   # FRED_API_KEY (free) and ANTHROPIC_API_KEY (or AGENTS_ANTHROPIC_API_KEY)
uv sync
uv run agents-run macro --dry-run   # fetch + compute; prints indicators, regimes, events. No LLM, no publish.
uv run agents-run macro             # full run: publishes to public-data/, updates data/
uv run pytest                        # 284 tests, no network
uv run python scripts/verify_macro_series.py   # confirms every configured FRED series resolves (live)
uv run python evals/run_macro.py     # §11 evals; real LLM calls, capped at $0.20
```

A run writes the agents-core data-branch contract to `public-data/` (`latest.json`, `history/`,
`manifest-entry.json`, `costs-summary.json`, `schema.json`) and its own state to `data/`
(`macro/state.json`, `costs.jsonl`, `guard_failures.jsonl`). In CI, `.github/workflows/agent-macro.yml`
calls agents-core's reusable `run-agent.yml`, which commits `data/` back and force-pushes `public-data/` to
this repo's `data` branch.

## Repo layout

```
agents/macro/     # agent.py (the agents-core Agent), config, fetch_fred, fetch_fed, fomc, transform,
                  # revisions, events, pipeline, build, display, analyze, templates, schema, state
config/           # macro.toml (indicators), fomc_dates.toml (fallback calendar), models.toml (tier override)
scripts/          # verify_macro_series.py, export_schema.py, fomc_gate.py (workflow gate)
evals/            # run_macro.py, scenario fixtures, real FOMC statement pairs + tone labels, results/
tests/            # unit + end-to-end tests; real FRED/federalreserve.gov responses as fixtures
data/             # committed run state (state.json, costs.jsonl, guard_failures.jsonl)
docs/             # specs/SPEC_MACRO.md (the build spec), agents/macro.md (eval summary)
```

## Status

See [`STATUS.md`](STATUS.md) for what's done, test count, measured run cost, eval results, and the few
things that need doing by hand (repository secrets and Actions settings).
