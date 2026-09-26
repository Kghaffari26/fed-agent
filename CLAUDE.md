# fed-agent

The macro & Fed agent, one repo in a multi-repo family (`agents-core` is the shared package; the site repo
is `Kghaffari26/agents-hub`). The full build spec is [`docs/specs/SPEC_MACRO.md`](docs/specs/SPEC_MACRO.md)
— read it before changing behavior described in a numbered section (§N below refers to it).

## Rules

- **Never write your own http/llm/costs/guards/publish/runner module.** All of that comes from
  `agents-core` (pinned `v0.1.0` in pyproject.toml): `agents_core.http.Http` (fetch_fred/fetch_fed take it),
  `agents_core.llm.LLM` + `agents_core.guards.fields_guard` (analyze.py), `agents_core.costs`,
  `agents_core.publish`, and the runner. Don't modify agents-core from here; if it's missing something,
  record it in STATUS.md.
- **The agent is `agents/macro/agent.py:AGENT`**, registered under the `agents_core.agents` entry point in
  pyproject.toml. `fetch` does all network I/O through `ctx.http`, `transform` is pure (and is where
  `--dry-run` stops), `analyze` makes the LLM calls and then saves `data/macro/state.json`.
- **Numbers come from data, never from the model.** Every figure in `transform.py`, `revisions.py`,
  `events.py`, `pipeline.py`, `build.py` and `fomc.py` is computed in plain Python and unit-tested. Event
  facts are rounded by `display.py` to published precision, so the LLM sees exactly what the site shows.
  `analyze.py` only writes narrative, through `ctx.llm.structured(..., guard=fields_guard(...),
  fallback=<templates.py>)`: a guard failure retries once, then the template ships with
  `narrative_source: "template"`. Citations are attached by code, never by the model.
- **Zero LLM calls on a no-change day.** Events are only emitted for series whose FRED `last_updated`
  moved, for a statement/minutes not yet in state, or on a regime/delayed/curve transition. No events →
  no LLM call; the brief, FOMC read and minutes summary are reused from `data/macro/state.json`
  (committed: agents-core's run-agent.yml commits `data/` but doesn't restore `public-data/`).
- **§6 is the site contract.** `agents/macro/schema.py`'s `MacroOutput` (an `agents_core.schema.AgentOutput`,
  so `meta` is agents-core's `RunMeta`) is the source of truth; regenerate `schemas/macro.schema.json` with
  `uv run python scripts/export_schema.py` after changing it (tests/test_schema.py snapshot-checks it). The
  runner also publishes it as `public-data/schema.json`.
- Round only at publish time (§5.1): transforms return full precision; `display.py` rounds.
- **No live network calls in tests.** FRED and federalreserve.gov fixtures are real responses recorded on
  2026-09-26 (tests/fixtures/); tests serve them through respx or `httpx.MockTransport` into a real
  `agents_core.http.Http`, and use `tests/macro_fakes.FakeAnthropic` for the LLM. Live calls (verify
  script, evals, real runs) are deliberate, separate steps.
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
uv run python evals/run_macro.py [--no-llm]    # §11 evals, real LLM calls capped at $0.20
```

## Module map

| Module | Spec section | Role |
|---|---|---|
| `agent.py` | §4, §10 | The agents-core `Agent`: fetch / transform / analyze, state, no-change path |
| `config.py` | §2, §8 | macro.toml / fomc_dates.toml models (incl. display units per indicator) |
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
| `analyze.py` | §7.1-§7.3 | The three guarded LLM calls |
| `schema.py` | §6 | `MacroOutput` (agents-core `AgentOutput`) |
| `state.py` | §4 | `data/macro/state.json` |
