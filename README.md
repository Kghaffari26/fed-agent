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

### Sample output shape (see SPEC_MACRO.md §6 for the full schema)

```json
{
  "headline": "August CPI rose 2.9% YoY (prior 2.7%); core PCE 3-mo annualized cooled to 2.4%.",
  "regimes": {
    "inflation": {"label": "Cooling", "detail": "Core PCE 2.8% YoY; 2.4% 3-mo annualized"},
    "policy": {"label": "Holding", "detail": "Target range 4.00-4.25%"}
  },
  "brief": {
    "bullets": [{
      "text": "Headline CPI rose to 2.9% YoY in August from 2.7%, while core CPI held at 3.1%.",
      "event_ids": ["new_release:CPIAUCSL:2026-08"],
      "citations": [{"name": "FRED: CPIAUCSL", "url": "https://fred.stlouisfed.org/series/CPIAUCSL"}]
    }],
    "narrative_source": "llm"
  }
}
```

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

Budgeted at **$0.30-0.50/month** at the normal cadence (SPEC_MACRO.md §12): ~$0.02 for a what-changed brief
(~12 change-days/month), ~$0.02 for an FOMC read (~0.7/month), ~$0.05 for a minutes summary (~0.7/month).
`MAX_RUN_USD=0.25` caps any single run.

## Setup

```bash
cp .env.example .env   # fill in FRED_API_KEY (free) and ANTHROPIC_API_KEY
uv sync
uv run pytest
uv run python scripts/verify_macro_series.py   # confirms every configured FRED series resolves
```

## Repo layout

```
agents/macro/     # config, fetch_fred, fetch_fed, transform, revisions, events, fomc, analyze,
                   # templates, schema, state, pipeline
config/            # macro.toml (indicators), fomc_dates.toml (fallback calendar)
scripts/           # verify_macro_series.py, export_schema.py
evals/macro/       # style/event-grounding checks, scenario fixtures, proposed FOMC-tone labels
tests/             # unit tests + fixtures (FRED response shapes, FOMC statement HTML, RSS feed)
docs/specs/        # SPEC_MACRO.md, the build spec
```

## Status

See [`STATUS.md`](STATUS.md) for exactly what's done, what's blocked and why (this environment's network
policy and a pending `agents-core` dependency — both explained there), test counts, eval results, and the
concrete next steps.
