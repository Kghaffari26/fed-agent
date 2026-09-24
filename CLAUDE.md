# fed-agent

The macro & Fed agent, one repo in a multi-repo family (`agents-core` is the shared package; the site repo
is `Kghaffari26/agents-hub`). The full build spec is [`docs/specs/SPEC_MACRO.md`](docs/specs/SPEC_MACRO.md)
— read it before changing behavior described in a numbered section (§N below refers to it).

## Rules

- **Never write your own http/llm/costs/guards/publish/runner module.** All of that comes from
  `agents-core` (`agents_core.http`, `agents_core.llm`, `agents_core.guards`, `agents_core.costs`,
  `agents_core.publish`, `agents_core.runner`/`registry`). Every function here that needs one of those takes
  it as an injected callable (see `fetch_fred.GetFn`, `fetch_fed.GetFn`, `analyze.GuardedCall`) so the real
  implementation can be wired in without rewriting this repo's logic. See STATUS.md for the current
  `agents-core` dependency state before assuming it's installed.
- **Numbers come from data, never from the model.** Every figure in `agents/macro/transform.py`,
  `revisions.py`, `events.py`, and `fomc.py` is computed in plain Python and unit-tested against
  hand-computed fixtures. The LLM (`analyze.py`) only writes narrative around numbers it's given, and every
  number it writes is checked against those facts via the number guard (§7.4) before publishing — a
  mismatch retries once, then falls back to `templates.py`.
- **Zero LLM calls on a no-change day.** `fetch_fred.has_series_changed()` compares FRED's `last_updated`
  before anything else; `state.py` persists it in `data/macro/state.json` (committed, not published).
- **§6 is the site contract.** `agents/macro/schema.py`'s `MacroOutput` is the source of truth; regenerate
  `schemas/macro.schema.json` with `uv run python scripts/export_schema.py` after changing it, and keep
  `tests/test_schema.py`'s snapshot check in sync.
- Round only at publish time (§5.1) — transforms return full precision.
- All HTTP fixtures are hand-built or respx-mocked to match real response shapes; **no live network calls
  in tests**. Live calls (to record fixtures, verify series, or an actual run) are a deliberate, separate
  step — see `scripts/verify_macro_series.py` and STATUS.md.

## Before you start

Read `STATUS.md` first. As of the last update it documents two blockers specific to this environment that
may or may not still apply:

1. **`agents-core`'s installable shape.** This repo depends on `agents-core` publishing
   `src/agents_core/{guards,registry}.py` (or later) as an installable package — see the pinning recipe in
   STATUS.md. Until then, `analyze.py`'s LLM calls, the actual `agents_core.agent.Agent` registration, and
   the reusable `run-agent.yml` workflow can't be wired up for real.
2. **Network/API-key access** to `api.stlouisfed.org` (FRED) and `www.federalreserve.gov` may be restricted
   in a given sandboxed environment. If so, `scripts/verify_macro_series.py` and live FOMC fixture capture
   will fail even with correct code — that's an environment setting, not a bug.

If both are resolved, STATUS.md's "Steps for the morning" section is the concrete unblock checklist.

## Commands

```bash
uv sync
uv run pytest                                  # all tests, no network
uv run ruff check .
uv run python scripts/verify_macro_series.py   # needs FRED_API_KEY + network access
uv run python scripts/export_schema.py         # regenerate schemas/macro.schema.json
uv run python evals/run_macro.py               # runs what's runnable now; see docs/agents/macro.md
```

## Module map

| Module | Spec section | Needs agents-core? |
|---|---|---|
| `config.py` | §2, §8 | No |
| `fetch_fred.py` | §3, §5.5 | Only the injected `get` transport |
| `fetch_fed.py` | §3, §5.7 step 1 | Only the injected `get` transport |
| `fomc.py` | §5.7 steps 2-5 | No |
| `transform.py` | §5.1, §5.4 | No |
| `revisions.py` | §5.3 | No |
| `events.py` | §5.5, §5.6 | No |
| `pipeline.py` | §4 (transform→events glue) | No |
| `state.py` | §4 | No |
| `templates.py` | headline + guard fallback text | No |
| `analyze.py` | §7.1-§7.3 | Yes — the actual `guarded_call`/LLM wiring |
| `schema.py` | §6 | No |
