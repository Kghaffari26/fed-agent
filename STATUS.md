# Status

**2026-09-27.** agents-core is upgraded to **v0.3.0**. Every local workaround for a gap that v0.2.0 closed is
gone. The agents-hub reports are fixed, the GDP "delayed" false positive is fixed, and the agent has three new
pieces:

- a release investigator, built on `agents_core.agent_loop`;
- run tracing, published as `trace.json`;
- evals on `agents_core.evals`, with a history file and a PR gate.

See `DECISIONS.md` (2026-09-27 section) for each judgment call.

## agents-core v0.3.1 (2026-09-27, later)

- Pin, lockfile and both workflow `uses:` refs are on **v0.3.1**.
- **Fast-tier `temperature = 0` is back** (config/models.toml; only the eval judge uses that tier). v0.3.1
  sends it in `extra_body`. Live smoke check: one structured Haiku call, `extra_body={"temperature": 0.0}`,
  no `TypeError`; $0.0010 over two calls (the first one's cost logging failed in the scratch script).
  Smart stays unset: claude-sonnet-5 rejects sampling parameters.
- **`evals/run_macro.py` is gone.** Evals run through
  `agents-evals run evals.macro.suites:{TEMPLATES,BRIEF,FOMC_READ,INVESTIGATOR} --total-max-usd 0.60`;
  evals.yml passes `total_max_usd: "0.60"` and sets `AGENTS_CORE_GUARD_FAILURES_PATH` in `eval_command`.
- No test fake asserted `kwargs["temperature"]`, so no test changes. 312 tests and ruff pass.
- The two gaps below about temperature and the per-suite cap are fixed in v0.3.1.

## Done this session (v0.3.0)

### agents-core v0.3.0 (`uv add ...@v0.3.0`)

These workarounds were removed:

- **Warnings are published** in `meta.warnings` (`AgentResult.warnings`). They were only logged before.
  - A failed FRED series is now §10's `status: "ok"` with a warning, plus `stale: true` on its indicator. It
    used to be `status: "stale"`.
- **Conditional downloads.** The Fed RSS feed and calendar page use `Http.download` (ETag/Last-Modified). The
  old `ttl_seconds=0` and 24h-TTL fetches are gone.
- **No cached error responses.** agents-core now caches only 2xx responses, so the 30-day `series/release`
  cache moved out of `state.json` and into the HTTP cache (TTL 30 days). CI keeps `.cache/` with
  actions/cache.
- **Data-branch restore.** `run-agent.yml` now restores `public-data/`. The no-change path republishes the
  FOMC block, minutes, headline and investigation from `ctx.previous_latest()`, and `state.json` is back to
  its §4 fields.
  - Older state files' blocks are read once, as a migration fallback.
  - If no previous FOMC block exists anywhere, the latest statement and minutes are rediscovered from the
    feed.
  - `public-data` was dropped from `cache_path`.
- **Token passthrough / ops alerts.** A statement-extraction failure (§10) now opens a GitHub issue through
  `ctx.alert`. That needs `GITHUB_TOKEN` and `issues: write`.
- **HTTP caps.** No local cap existed to remove. The per-host `min_interval` policies stay.
- **Per-tier temperature.** Tried for the fast tier (the eval judge), but the installed SDK's
  `messages.parse` raises `TypeError` on `temperature`, so it stays unset (see gaps below).

The workflow now calls `run-agent.yml@v0.3.0`. The calling job grants `contents: write` and `issues: write`,
and `cache_path` is `.cache`. actionlint passes.

### agents-hub reports

- **No Anthropic key.** The run publishes `status: ok`. The brief, FOMC read, minutes and investigation all
  use their templates (`narrative_source: "template"`), and there is one warning:
  `"No Anthropic API key configured: published template narrative only"`. Tested end to end.
- **Formats.** Every `format`/`delta_format` is typed as agents-core's `StatFormat`, so the schema enforces
  it. A test checks every configured indicator and transform.

### GDP "delayed"

`delayed` now uses FRED's actual release status (`release/dates` without
`include_release_dates_with_no_data`). A release counts as delayed only when all of these hold:

- its last scheduled date is missing from FRED's published dates;
- that date is more than 2 days old;
- the series hasn't updated since.

On live data, GDP's 2026-08-26 release is in FRED's published list, so GDP is **not delayed**, and its next
release is 2026-09-30. A date that is scheduled but not yet due is never delayed. See case study 1.

### (A) Tracing

The runner traces automatically: `trace.json` and `trace.schema.json` are published, and `manifest-entry.json`
has a `trace_summary`. The agent adds a `macro:investigate` span. Tests assert:

- the trace files are published;
- the agent-loop and tool-call spans are present;
- the API key isn't in the trace.

### (B) Evals

`evals/macro/suites.py` has four suites: `macro-templates`, `macro-brief`, `macro-fomc-read` and
`macro-investigator`.

- `agents-evals run ... --total-max-usd` runs them all under one total cap (was `evals/run_macro.py`).
- Results go to `evals/results/2026-09-27.json`, and history to `evals/history.jsonl`.
- `.github/workflows/evals.yml` calls `run-evals.yml@v0.3.0` on PRs that touch `agents/`, `config/`,
  `evals/` or the lockfile, with `max_usd: "0.60"` and `regression_threshold: "0.10"`.

### (C) Release investigator

`agents/macro/investigate.py` implements the investigator; it is documented in SPEC §6.1 and published as
`investigation`, an additive field. Schema is 1.1.0.

- **Trigger.** It runs once per run, when there is a new CPI, core PCE, payrolls, unemployment or GDP release,
  or an FOMC decision.
- **Tools.** `get_series`, `get_components`, `percentile_vs_history`, `compare_to_prior_cycles` and
  `get_fomc_context`, plus the built-in `finish`.
- **Components.** 12 new component series in `config/macro.toml` (5 CPI components, 7 payroll sectors), all
  verified live.
- **Budget.** 8 steps, $0.08, 120 s.
- **Guard.** Checks the final `analysis` against the trigger facts plus every tool output.
- **Citations.** Attached by code, only for series a tool returned.
- **Failures.** Any loop failure publishes the template with a warning; the run doesn't fail.
- **Replay tests.** Three real trajectories replay offline in the tests.

### (D) and (E)

`docs/case-studies.md` has five incidents with commit links. `README.md` has Highlights and Demo sections, with
the demo files in `docs/demo/` from a real run.

## Test count

**312 tests passing.** `uv run ruff check .` and `uv run ruff format --check .` are clean, and the suite makes
no network calls. New tests:

- `test_investigator.py`: tools on real recorded data, the trigger, the template, and three real
  trajectories replayed with `ReplayClient`;
- `test_eval_suites.py`: the suites run offline against a fake client;
- `test_display.py`: formats;
- end-to-end tests for the investigator (LLM path, guard retry then template, budget stop), no key, a failed
  series, `trace.json`, the rediscovery path, and the previous-`latest.json` republish.

## Real runs and spend (2026-09-27)

| What | Cost |
|---|---|
| Real fresh-state run in a scratch dir: brief + FOMC read + minutes + investigation (FOMC trigger, 2 steps, 3 tool calls) | **$0.0550** (investigation $0.0087) |
| Immediate re-run | **$0.0000**: zero LLM calls; brief and investigation `reused_from_run_id` |
| Evals: first investigator run (the judge crashed on `temperature`) | $0.0376 |
| Evals: all four suites | $0.1300 |
| Evals: investigator after the prompt fix | $0.0409 |

**Anthropic spend this session: $0.26** (budget $1.50). Eval spend is in `data/eval_costs.jsonl`. The demo
run's costs stayed in its scratch data dir, so this repo's `data/costs.jsonl` is unchanged.

The repo's own `data/macro/state.json` was deliberately **not** rewritten this session. It still carries the
legacy FOMC and minutes blocks, and CI's first v0.3.0 run needs them, because this repo has no `data` branch
yet. That run migrates the state automatically.

## Eval results (2026-09-27)

| Suite | Pass rate | Scores |
|---|---|---|
| macro-investigator | 1.00 | trajectory scorers 1.00, numbers 1.00, citations 1.00, LLM judge 1.00 (0.65 before the prompt fix) |
| macro-brief | 1.00 | number fidelity 1.00, first-attempt guard pass 1.00, style 1.00, grounding 1.00 |
| macro-fomc-read | 0.83 | tone 0.83 (PROVISIONAL labels; the arguable 2023-12→2024-01 pair), phrases 1.00, cited idx 1.00 |
| macro-templates | 1.00 | style 1.00, grounding 1.00 |

## Things to do by hand

1. **Repository secrets** (Settings → Secrets and variables → Actions). Both workflows use `secrets: inherit`.
   - `FRED_API_KEY` is required.
   - `ANTHROPIC_API_KEY` is strongly recommended. Without it the agent still runs and publishes template text
     with a warning, and the PR evals can't run their LLM suites.
   - `SITE_DISPATCH_TOKEN` is optional.
2. **Actions permissions.** The calling jobs now grant `contents: write` and `issues: write` themselves. If an
   org policy caps the default token at read-only, allow these workflows to request write, or the push steps
   fail. If `main` gets branch protection, let the Actions bot push.
3. **If `Kghaffari26/agents-core` is private**, allow access to repositories owned by the account (Settings →
   Actions → General → Access), or the `uses:` lines won't resolve.
4. **Confirm the FOMC tone labels** in `evals/macro/labels_proposed.json`, then flip `status`.
5. **Run the Macro agent workflow once** (workflow_dispatch). That creates the `data` branch. From then on
   the no-change path reads the previous `latest.json`.

## agents-core: gaps found (none blocking; agents-core not modified)

- **(Fixed in v0.3.1.) Per-tier `temperature` breaks structured calls.** `LLM.structured` passes `temperature` to
  `client.messages.parse`. With the installed anthropic SDK that raises
  `TypeError: Messages.parse() got an unexpected keyword argument 'temperature'`, seen live with the eval
  judge on the fast tier. `complete`/`converse` (`messages.create`) are unaffected. The fix belongs in
  agents-core: pass it through `extra_body`, or pin an SDK version that supports it.
- **(Fixed in v0.3.1.) `agents-evals run` caps each suite separately.** A repo with several suites needs its own wrapper for one
  total cap: here, `evals/run_macro.py` passes each suite what's left.
- **`LLMJudge` renders `case.input` verbatim.** For a loop whose real input differs from the fixture, the case
  input has to be rebuilt to what the loop saw, or the judge grades against the wrong numbers (case study 5).
  An `input=` selector like the existing `output=` would help.

## Not done

- `/macro` page rendering of `investigation` lives in `Kghaffari26/agents-hub`, which is additive and ignored
  until the site adds it.
- The BLS fallback (`use_bls_fallback`) stays off, as the spec defaults it.
