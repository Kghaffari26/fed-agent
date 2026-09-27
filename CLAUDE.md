# fed-agent

The macro & Fed agent, one repo in a multi-repo family (`agents-core` is the shared package; the site repo
is `Kghaffari26/agents-hub`). The full build spec is [`docs/specs/SPEC_MACRO.md`](docs/specs/SPEC_MACRO.md)
— read it before changing behavior described in a numbered section (§N below refers to it).

## Rules

- **Never write your own http/llm/costs/guards/publish/runner/loop/tracing/evals module.** All of that comes
  from `agents-core` (pinned `v0.3.0` in pyproject.toml): `agents_core.http.Http` (fetch_fred/fetch_fed take
  it; `Http.download` for conditional GETs), `agents_core.llm.LLM` + `agents_core.guards.fields_guard`
  (analyze.py), `agents_core.agent_loop` (investigate.py), `agents_core.tracing` (trace.json is automatic),
  `agents_core.evals` (evals/macro/suites.py), `agents_core.costs`, `agents_core.publish`, and the runner. Don't modify agents-core from here; if it's missing something,
  record it in STATUS.md.
- **The agent is `agents/macro/agent.py:AGENT`**, registered under the `agents_core.agents` entry point in
  pyproject.toml. `fetch` does all network I/O through `ctx.http`, `transform` is pure (and is where
  `--dry-run` stops), `analyze` makes the LLM calls (and at most one agent loop) and then saves
  `data/macro/state.json`. Non-fatal problems go in `AgentResult.warnings` → `meta.warnings`.
- **Numbers come from data, never from the model.** Every figure in `transform.py`, `revisions.py`,
  `events.py`, `pipeline.py`, `build.py` and `fomc.py` is computed in plain Python and unit-tested. Event
  facts are rounded by `display.py` to published precision, so the LLM sees exactly what the site shows.
  `analyze.py` only writes narrative, through `ctx.llm.structured(..., guard=fields_guard(...),
  fallback=<templates.py>)`: a guard failure retries once, then the template ships with
  `narrative_source: "template"`. Citations are attached by code, never by the model.
- **The release investigator (§6.1, `investigate.py`)** is an `AgentLoop` (8 steps, $0.08, 120 s) that runs
  at most once per run, on a new CPI/core PCE/payrolls/unemployment/GDP release or FOMC decision. Its tools
  only read data `transform` computed (rounded by `display.py`); its guard checks `analysis` against the
  trigger facts + every tool output; any loop failure ships `template_investigation` with a warning, never a
  failed run. `build_loop` is shared with the evals. Bump `PROMPT_VERSION` when its prompt changes.
- **No Anthropic key is not an error**: `llm_available()` is false → every narrative is its template,
  `status: ok`, one warning.
- **Zero LLM calls on a no-change day.** Events are only emitted for series whose FRED `last_updated`
  moved, for a statement/minutes not yet in state, or on a regime/delayed/curve transition. No events →
  no LLM call; the brief comes from `data/macro/state.json` (§4), and the FOMC block, minutes, headline and
  investigation from the previous `latest.json` (`ctx.previous_latest()`; run-agent.yml restores the data
  branch). Legacy state fields are a read-only migration fallback.
- **§6 is the site contract.** `agents/macro/schema.py`'s `MacroOutput` (an `agents_core.schema.AgentOutput`,
  so `meta` is agents-core's `RunMeta`) is the source of truth; regenerate `schemas/macro.schema.json` with
  `uv run python scripts/export_schema.py` after changing it (tests/test_schema.py snapshot-checks it). The
  runner also publishes it as `public-data/schema.json`.
- Round only at publish time (§5.1): transforms return full precision; `display.py` rounds.
- **No live network calls in tests.** FRED and federalreserve.gov fixtures are real responses recorded on
  2026-09-26 (tests/fixtures/); tests serve them through respx or `httpx.MockTransport` into a real
  `agents_core.http.Http`, and use `tests/macro_fakes.FakeAnthropic` for the LLM (`parse` for structured
  calls, a scripted `create` for the agent loop). Real loop trajectories replay through
  `agents_core.agent_loop.ReplayClient` (tests/fixtures/investigator/). Live calls (verify script,
  record_eval_series, evals, real runs) are deliberate, separate steps.
- Every `format`/`delta_format` must be an agents-core `StatFormat` (typed in schema.py; tests/test_display.py).
- Log every judgment call made while working autonomously as one line in DECISIONS.md.

## Before you start

Read `STATUS.md` (current state, run cost, what needs doing by hand) and `DECISIONS.md`.

## Commands

```bash
uv sync
uv run pytest                                  # all tests, no network
uv run ruff check .
uv run agents-run macro --dry-run              # live fetch + compute, no LLM, nothing published
uv run agents-run macro                        # real run (FRED_API_KEY + ANTHROPIC_API_KEY/AGENTS_ANTHROPIC_API_KEY)
uv run python scripts/verify_macro_series.py   # live FRED check of every configured series
uv run python scripts/export_schema.py         # regenerate schemas/macro.schema.json
uv run python evals/run_macro.py [--no-llm]    # all eval suites, one total cap ($0.60 default)
uv run agents-evals compare --threshold 0.10   # score deltas vs the previous evals/history.jsonl entry
uv run python scripts/record_eval_series.py    # re-record the investigator evals' real FRED history
```

## Module map

| Module | Spec section | Role |
|---|---|---|
| `agent.py` | §4, §10 | The agents-core `Agent`: fetch / transform / analyze, state, no-change path |
| `config.py` | §2, §8 | macro.toml / fomc_dates.toml models (display units; `[[component]]` series) |
| `fetch_fred.py` | §3 | FRED via `agents_core.http.Http`; `last_updated` change detection |
| `fetch_fed.py` | §3, §5.7 step 1 | RSS, statement/minutes pages (cached permanently), meeting calendar |
| `fomc.py` | §5.7 steps 2-6 | Extraction, decision/vote parsing, sentence diff, minutes text |
| `transform.py` | §5.1, §5.2, §5.4 | Transforms, series/resampling, regimes, inversions, sign changes |
| `revisions.py` | §5.3 | Raw revision detection |
| `events.py` | §5.5, §5.6 | Event constructors and ranking |
| `pipeline.py` | §4-§5.6 | Per-indicator snapshot + events (revisions in displayed terms) |
| `build.py` | §5.4-§6 | Indicator/regime/yield-curve/calendar/key-stat/FOMC blocks |
| `display.py` | §5.1 | Publish-time rounding and §6 formats |
| `templates.py` | §6, §7.4 | Headline + every narrative fallback |
| `analyze.py` | §7.1-§7.3 | The three guarded LLM calls; `llm_available` (no-key path) |
| `investigate.py` | §6.1 | The release investigator: trigger, tools, `AgentLoop`, template fallback |
| `schema.py` | §6, §6.1 | `MacroOutput` (agents-core `AgentOutput`), incl. `investigation` |
| `state.py` | §4 | `data/macro/state.json` |
| `evals/macro/suites.py` | §11 | `agents_core.evals` suites (templates, brief, FOMC read, investigator) |
