# fed-agent

An agent that tracks the key U.S. macroeconomic indicators and Federal Reserve communications, and explains
**what changed since the last release** in plain English — grounded entirely in numbers computed in code,
never numbers produced by a model. Built from [`docs/specs/SPEC_MACRO.md`](docs/specs/SPEC_MACRO.md) for
the `fed-agent` slot in a small family of scheduled data agents (see `agents-core` / `agents-hub`).

## Highlights

- **An agent loop that investigates, within budget.** When a big release lands (CPI, core PCE, payrolls,
  unemployment, GDP, or an FOMC decision), a tool-use loop built on `agents_core.agent_loop` explains what's
  driving it. It can pull CPI components and payrolls by sector, rank the reading against 10 years of
  history, compare it with the 2019 and 2022-23 cycles, and read the Fed's context. It must end with a
  `finish` tool, and it is capped at 8 steps, $0.08 and 120 s. A budget stop publishes a template with a
  warning, never a failed run. The result is published as `investigation` in `latest.json`
  ([SPEC §6.1](docs/specs/SPEC_MACRO.md)).
- **Guards on everything the model writes.** Every number in the brief, the FOMC read, the minutes summary
  and the investigator's final text is checked against the facts and tool outputs the model was given. A
  failure gets one retry and then a deterministic template, labeled `narrative_source: "template"`.
  Citations are attached by code (the investigator's only for series a tool actually returned). See
  [case study 4](docs/case-studies.md#4-the-model-helpfully-converted-14-point-into-25-basis-point).
- **Evals as a PR gate, with history.** Four `agents_core.evals` suites cover the template brief, the LLM
  brief, the FOMC read and the investigator. The investigator suite runs trajectory scorers (required tools,
  forbidden tools, max steps, stop reason) and an LLM judge. Every run appends to `evals/history.jsonl`, and
  `.github/workflows/evals.yml` fails a PR whose scores regress. Latest scores (2026-09-27, real calls):

  | Suite | Pass rate | Scores | Cost |
  |---|---|---|---|
  | `macro-investigator` (5 scenarios) | **1.00** | required/forbidden tools, max steps, stop reason, trigger, numbers, citations: 1.00; **LLM judge 1.00** | $0.041 |
  | `macro-brief` (5 scenarios) | **1.00** | number fidelity 1.00, first-attempt guard pass 1.00, style 1.00, grounding 1.00 | $0.022 |
  | `macro-fomc-read` (6 real statement pairs) | 0.83 | tone 0.83 (provisional labels), phrases verbatim 1.00, cited changes 1.00 | $0.078 |
  | `macro-templates` | 1.00 | style 1.00, grounding 1.00 | $0 |

- **Tracing on every run.** agents-core's tracer records the run, each phase, every HTTP request and LLM
  call (tokens, cost, stop reason), the agent loop with each tool call, and each guard outcome. The run
  publishes this as `trace.json` on the data branch, with a `trace_summary` in `manifest-entry.json`. The agent
  adds its own `macro:investigate` span. See [`docs/demo/trace-excerpt.json`](docs/demo/trace-excerpt.json).
- **Costs measured, capped and mostly zero.** A worst-case first run (brief, FOMC read, minutes summary and
  investigation) cost **$0.055**; the investigation itself was **$0.0087**. An immediate re-run costs
  **$0.00**, because a no-change day makes zero LLM calls. Each run is capped at $0.25, and the eval run is
  capped at $0.60 in total.

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
5. On a new high-priority release or FOMC decision, runs the release investigator (above) once.

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

## Demo

Real output from a fresh-state run on 2026-09-27
([`docs/demo/latest-excerpt.json`](docs/demo/latest-excerpt.json)). The FOMC's September hike triggered the
release investigator, which made 2 model steps and 3 tool calls and cost $0.0087:

```json
"investigation": {
  "trigger": {"event_id": "fomc_decision:2026-09-16", "type": "fomc_decision", "indicator_id": null},
  "analysis": "The FOMC raised its target range by 25bp to 3.75-4.00% on 2026-09-16, a unanimous (12-0) decision with a tone described as more hawkish than the prior meeting. This follows CPI inflation that has picked up from 2.4% in February 2026 to 4.2% in May 2026 before easing slightly to 3.3% in July and 3.4% in August, still well above the 2.4-2.7% readings seen in late 2025. Meanwhile the unemployment rate has declined from 4.5% in November 2025 to 4.1% in August 2026, indicating a tighter labor market alongside firmer inflation.",
  "cited_series": [{"id": "cpi", "fred_series": "CPIAUCSL", "url": "https://fred.stlouisfed.org/series/CPIAUCSL", "...": "..."},
                   {"id": "unrate", "fred_series": "UNRATE", "...": "..."}],
  "narrative_source": "llm",
  "loop": {"steps": 2, "tool_calls": ["get_fomc_context", "get_series", "get_series"],
           "stop_reason": "finished", "cost_usd": 0.008654, "guard_attempts": 1}
}
```

The same run's trace ([`docs/demo/trace-excerpt.json`](docs/demo/trace-excerpt.json)):

```
run macro                                  63.4 s   $0.0550
├─ phase fetch                             41.3 s   (FRED + federalreserve.gov, conditional GETs)
├─ phase transform                          0.1 s
├─ phase analyze                           22.0 s
│  ├─ llm_call macro:brief                  5.6 s   $0.0080
│  ├─ llm_call macro:fomc_read              5.7 s   $0.0092
│  ├─ llm_call macro:minutes                5.0 s   $0.0291
│  └─ custom macro:investigate              5.6 s
│     └─ agent_loop macro:investigator              $0.0087  stop_reason=finished
│        ├─ llm_call step1 → tool_call get_fomc_context, get_series ×2
│        ├─ llm_call step2 → finish
│        └─ guard macro:investigator:finish          outcome=pass
└─ phase publish                            0.0 s
```

An immediate re-run made zero LLM calls ($0.00): the brief and the investigation were republished with
`reused_from_run_id` pointing at the run above.

Try it yourself (a real fresh-state run into a scratch directory, about $0.06):

```bash
export AGENTS_CORE_DATA_DIR=/tmp/macro/data AGENTS_CORE_PUBLISH_DIR=/tmp/macro/public-data MACRO_OBS_CACHE_DIR=/tmp/macro/obs
uv run agents-run macro
jq .investigation /tmp/macro/public-data/latest.json
jq '.spans[] | select(.kind=="agent_loop" or .kind=="tool_call") | {kind, name, attrs}' /tmp/macro/public-data/trace.json
```

Without an Anthropic key the same command still publishes, with `status: "ok"`, template narrative everywhere
and one entry in `meta.warnings`.

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

Measured on real runs (2026-09-27): a worst-case first run (brief, FOMC read, minutes summary and
investigation) cost **$0.055**, and an immediate re-run **$0.00** (zero LLM calls). A normal release day is
the brief, about $0.008, plus an investigation of about $0.01 when the release is a high-priority one.
SPEC_MACRO.md §12 budgets $0.30-0.50 a month. `max_run_usd: 0.25` caps any single run, and the investigator
has its own $0.08 cap inside that.

## Running it

`agents-core` (the shared framework: HTTP, LLM, number guard, costs, publishing, runner) is a dependency
at v0.3.2, pinned by commit SHA `9e4f342` until the `v0.3.2` tag exists (then the pyproject.toml and
workflow pins switch to the tag). This repo registers the agent through the `agents_core.agents` entry point, so:

```bash
cp .env.example .env   # FRED_API_KEY (free) and ANTHROPIC_API_KEY (or AGENTS_ANTHROPIC_API_KEY)
uv sync
uv run agents-run macro --dry-run   # fetch + compute; prints indicators, regimes, events. No LLM, no publish.
uv run agents-run macro             # full run: publishes to public-data/, updates data/
uv run pytest                        # 317 tests, no network
uv run python scripts/verify_macro_series.py   # confirms every configured FRED series resolves (live)
uv run agents-evals run evals.macro.suites:{TEMPLATES,BRIEF,FOMC_READ,INVESTIGATOR} --total-max-usd 0.60
                                     # all eval suites; real LLM calls, one $0.60 total cap
uv run agents-evals compare          # score deltas vs the previous history entry
```

A run writes the agents-core data-branch contract to `public-data/` (`latest.json`, `history/`,
`manifest-entry.json`, `costs-summary.json`, `schema.json`, `trace.json`, `trace.schema.json`) and its own
state to `data/` (`macro/state.json`, `costs.jsonl`, `guard_failures.jsonl`). In CI,
`.github/workflows/agent-macro.yml` calls agents-core's reusable `run-agent.yml` (v0.3.2, by SHA; granting
`contents: write` and `issues: write`). That workflow restores the `data` branch into `public-data/`, runs the
agent, commits `data/` back, and force-pushes `public-data/` to the `data` branch.
`.github/workflows/evals.yml` calls `run-evals.yml` (v0.3.2, by SHA) on pull requests.

## Repo layout

```
agents/macro/     # agent.py (the agents-core Agent), config, fetch_fred, fetch_fed, fomc, transform,
                  # revisions, events, pipeline, build, display, analyze, investigate (the agent loop),
                  # templates, schema, state
config/           # macro.toml (indicators), fomc_dates.toml (fallback calendar), models.toml (tier override)
scripts/          # verify_macro_series.py, export_schema.py, fomc_gate.py (workflow gate),
                  # record_eval_series.py (real FRED history for the investigator evals)
evals/            # macro/suites.py (agents_core.evals suites), scenario fixtures, real FOMC
                  # statement pairs + tone labels, history.jsonl, results/
tests/            # unit + end-to-end tests; real FRED/federalreserve.gov responses as fixtures
data/             # committed run state (state.json, costs.jsonl, guard_failures.jsonl)
docs/             # specs/SPEC_MACRO.md (the build spec), case-studies.md, demo/, agents/macro.md (evals)
```

## Status

See [`STATUS.md`](STATUS.md) for what's done, test count, measured run cost, eval results, and the few
things that need doing by hand (repository secrets and Actions settings).
