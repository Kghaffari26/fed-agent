# Status

Worked autonomously overnight per instructions. See `DECISIONS.md` for the one-line rationale behind every
judgment call made along the way; commits are one per completed step, all pushed to
`claude/tender-franklin-at8qn8`.

**Final update (09:15 UTC):** the 4-hour agents-core recheck window (started ~05:24 UTC) has closed.
`Kghaffari26/agents-core@main` was checked roughly every 30 minutes throughout and never moved off commit
`f79b6aa113857f34c5ac8c2381cc2f3064d4506e` — still the old monorepo shape, no `src/agents_core/{guards,
registry}.py` on any branch. Per the original instructions, rechecking has stopped; nothing further is
blocked on time, only on the two items under "Blockers" below. This file is the final handoff — everything
else in the repo (215 tests, ruff clean, all commits pushed) reflects the complete state of overnight work.

## Done

Everything in SPEC_MACRO.md §14's build order that doesn't require `agents-core` to be installable or live
network access to `federalreserve.gov`/`api.stlouisfed.org` — which turned out to be almost everything
except the final wiring:

- **Repo scaffolding**: multi-repo layout (`agents/macro/`, `config/`, `scripts/`, `evals/macro/`,
  `docs/specs/`), uv-managed `pyproject.toml` (Python 3.12), `.env.example`.
- **Config** (§2, §8): `config/macro.toml` with all 25 indicator blocks, `config/fomc_dates.toml` (fallback
  calendar — **placeholder dates, see "Blockers"**), pydantic models in `agents/macro/config.py`.
- **FRED fetch** (§3, §5.5): `fetch_fred.py` — series metadata, observations (`"."` → `None`), release-date
  lookups, `last_updated` change detection. Takes an injected `get` callable rather than its own
  retry/rate-limit/cache layer.
- **`scripts/verify_macro_series.py`**: written and exercises its no-key failure path; **not run live**
  (see "Blockers").
- **Transforms, revisions, regimes, delayed-data, events** (§5.1-§5.6): fully real, fully tested, in
  `transform.py`, `revisions.py`, `events.py`.
- **FOMC processing** (§3, §5.7): `fetch_fed.py` (RSS parsing) and `fomc.py` (extraction, decision/vote
  parsing incl. fractional ranges, pysbd+difflib sentence diff). Test fixtures are **hand-reconstructed**,
  not live captures — see `tests/fixtures/fomc/README.md` and "Blockers".
- **§6 output schema**: `schema.py` (pydantic), exported to `schemas/macro.schema.json`, validated against a
  fixture matching the spec's own worked example.
- **`templates.py`**: deterministic headline rendering and the guard-fallback template brief.
- **`state.py`** (§4): `data/macro/state.json` read/write, 36-observation trimming, and the no-change path
  (`has_series_changed` returning `False` is what lets a real runner skip both the fetch and the LLM call).
- **`analyze.py`** (§7.1-§7.3): prompt building, `collect_facts` for the guard, `attach_citations` (code
  attaches URLs, the model never does), `validate_tone_shift`, `filter_verbatim_key_phrases`. The actual LLM
  call is behind an injected `guarded_call` matching `agents_core.llm.call_with_number_guard`'s expected
  shape — ready to wire in, not yet wired.
- **`pipeline.py`**: per-indicator glue (fetched series → computed values → new_release/revision/
  threshold_cross events), the piece a real runner calls between fetch and analyze.
- **Evals** (§11): `style_check.py` and `event_grounding.py` run for real (100% pass, 5/5 fixtures) against
  the deterministic template brief. `labels_proposed.json`: 5 **real** historical FOMC meeting pairs with
  proposed tone-shift labels, **PROVISIONAL** — see "Eval results" below.
- **`.github/workflows/agent-macro.yml`** (§9): both crons + `workflow_dispatch`, calling
  `Kghaffari26/agents-core/.github/workflows/run-agent.yml@main` with `agent=macro`, `max_run_usd=0.25`,
  `site_repo=Kghaffari26/agents-hub`. **Won't run successfully yet** — see "Blockers".
- **README.md, CLAUDE.md**: written and current.
- **ruff**: clean. **Tests**: all passing.

## Not done / blocked

- Actual `agents_core.agent.Agent` registration, `uv run agents-run macro [--dry-run]`, a real run, and the
  `run-agent.yml` reusable workflow actually functioning — all wait on `agents-core`'s installable package
  (see below).
- Live FRED verification, live FOMC fixture capture, and confirming the proposed FOMC-tone labels against
  real statement text — all wait on this environment's network policy (see below).
- The real number-fidelity and phrase-verbatim evals (need a real LLM call).

## Test count

**215 tests passing**, `uv run ruff check .` clean. No live network calls anywhere in the suite (FRED and
FOMC RSS/HTML are respx-mocked or fixture-based).

## Eval results (provisional)

Full detail: `docs/agents/macro.md` and `evals/results/macro-2026-09-24.json`.

| Eval | Status | Result |
|---|---|---|
| Event grounding | Ran (real) | 100% (5/5 fixtures) |
| Style | Ran (real) | 100% (5/5 fixtures) |
| Number fidelity | Blocked | needs a real LLM call |
| Phrase verbatim | Blocked | needs a real LLM call |
| FOMC tone | **Provisional** | 5 real historical pairs labeled from training-data knowledge, not verified against live text |

## Run cost

**$0.00.** No Anthropic API calls were made this session — every LLM-dependent path is gated on
`agents-core` (see below), so there was nothing to spend against `MAX_RUN_USD`. Setting up the environment
(`uv sync`) and the git/GitHub work made no billed API calls either.

## agents-core SHA pinned

**None.** Checked `Kghaffari26/agents-core` repeatedly (every commit to `main` between session start and
now — currently `f79b6aa113857f34c5ac8c2381cc2f3064d4506e`, the only branch): it is still the old monorepo
shape (`core/registry.py`, `core/guards.py`, `core/llm.py` at the repo root, hardcoded `AGENT_IDS` tuple,
agents imported from `agents.<id>.agent` inside the *same* repo). No commit on any branch has
`src/agents_core/guards.py` + `src/agents_core/registry.py` — the installable, entry-point-based shape this
build needs. Its `run-agent.yml` reusable workflow also only accepts `agent`/`args` inputs today and checks
out agents-core itself, not a calling repo — incompatible with the multi-repo `max_run_usd`/`site_repo`
interface this build targets.

## Blockers

1. **`agents-core` isn't installable yet in the multi-repo shape.** Blocks: wiring `analyze.py`'s LLM calls
   for real, registering the agent with `agents_core.agents`, running `agents-run macro [--dry-run]`, a real
   run, and `run-agent.yml` actually succeeding in CI.
2. **This environment's network policy denies `api.stlouisfed.org` and `www.federalreserve.gov`** (confirmed
   403 at the egress proxy on a direct test — also true for arbitrary hosts like `www.google.com`, so it's a
   narrow allowlist, not something specific to these two hosts). There's also no `FRED_API_KEY` configured.
   Blocks: `scripts/verify_macro_series.py` running live, recording real FRED/FOMC fixtures, and verifying
   `config/fomc_dates.toml`'s placeholder meeting dates.

## Steps for the morning

1. **If `agents-core` is ready** (check with the same commands DECISIONS.md used, or just re-run the SHA
   search across its branches for `src/agents_core/{guards,registry}.py`): read its README/CLAUDE.md, pin it
   with `uv add "agents-core @ git+https://github.com/Kghaffari26/agents-core@<sha>"`, then:
   - Wire `analyze.py`'s `guarded_call` parameter to `agents_core.llm.call_with_number_guard`.
   - Add `agents/macro/agent.py` exposing `AGENT = Agent(id="macro", ...)` per `agents_core.agent`'s actual
     base class, using `pipeline.compute_indicator_snapshot` per indicator and `state.py` for persistence.
   - Confirm `.github/workflows/agent-macro.yml`'s `with:` block against agents-core's real `run-agent.yml`
     inputs (it may not be `max_run_usd`/`site_repo` — check the actual reusable workflow file).
   - Run `uv run agents-run macro --dry-run` and work through SPEC_MACRO.md §13's acceptance criteria.
2. **Broaden this environment's (or a future one's) network access** to `api.stlouisfed.org` and
   `www.federalreserve.gov`, and add a `FRED_API_KEY` (free) as an environment variable. Then:
   - `uv run python scripts/verify_macro_series.py` and fix anything it flags.
   - Fetch 3 real FOMC statement pages and replace `tests/fixtures/fomc/*.html` per that directory's
     README — re-run `uv run pytest tests/test_fomc.py tests/test_fomc_diff.py` to confirm nothing broke.
   - Verify `config/fomc_dates.toml` against `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm`
     and correct any wrong dates (only the Oct 27-28, 2026 meeting was taken from the spec's own example;
     the rest are placeholders).
   - Re-derive `evals/macro/labels_proposed.json`'s tone-shift labels from the real statement text for each
     of the 5 pairs, and flip its `status` from `PROVISIONAL` once confirmed.
3. Once both are unblocked, `uv run python evals/run_macro.py` again and update `docs/agents/macro.md`, then
   do one real run and report its actual cost (should be well under `MAX_RUN_USD=0.25`, per SPEC_MACRO.md's
   own ~$0.02-0.05/call estimates).
