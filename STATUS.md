# Status

Working autonomously overnight per instructions. Updated as work progresses; see `DECISIONS.md` for the
one-line rationale behind each judgment call.

## agents-core dependency

**Blocked, working around it.** `Kghaffari26/agents-core` (main @ `f79b6aa1`) is still the old monorepo
shape — `core/registry.py`, `core/guards.py`, `core/llm.py` etc. live at the repo root and are imported
directly (`from core.agent import Agent`), with agents registered in a hardcoded `AGENT_IDS` tuple and
loaded via `agents.<id>.agent` from *inside the same repo*. No commit on any branch yet has
`src/agents_core/guards.py` + `src/agents_core/registry.py` — the installable, entry-point-based package
shape this multi-repo build needs.

Rechecking periodically (see below). Meanwhile building every core-independent piece: config models,
transforms/revisions/regimes/events, and FOMC parsing/diff — all pure Python, fully tested against fixtures,
with no dependency on agents-core. `scripts/verify_macro_series.py` and the FOMC HTML fixtures are fetched
live with plain `httpx`, since they're one-off scripts rather than the reusable fetch module.

## Done

(updated as steps complete)

## Needed from agents-core

(filled in once the package is installed and gaps are found, or if the 4-hour window elapses first)

## Test count

TBD

## Provisional eval results

TBD

## Run cost

TBD

## agents-core SHA pinned

None yet — see above.

## Blockers

- `agents-core` not yet in the installable shape (`src/agents_core/{guards,registry}.py`) required by the
  multi-repo instructions. See "agents-core dependency" above.
- **This environment's network policy blocks essentially all external hosts except GitHub, package
  registries (PyPI/npm/etc.) and `api.anthropic.com`.** Confirmed by direct test: `api.stlouisfed.org`
  (FRED) and `www.federalreserve.gov` both get a `403` at the egress proxy ("CONNECT tunnel failed"),
  and so does `www.google.com` — this is a network allowlist, not a FRED-key problem (there's also no
  `FRED_API_KEY` configured, which would matter too once the host is reachable). This blocks, completely,
  in this environment:
  - `scripts/verify_macro_series.py` running live against FRED
  - recording real FRED response JSON as fixtures
  - fetching the real FOMC RSS feed / statement pages / meeting calendar from federalreserve.gov
  - the "≥3 real statement pages as HTML fixtures" requirement

  **To unblock: broaden this environment's Network access (cloud environment menu → Edit) to allow
  `api.stlouisfed.org` and `www.federalreserve.gov` (or a less restrictive access level), and add
  `FRED_API_KEY` (free at https://fred.stlouisfed.org/docs/api/api_key.html) as an environment variable.**
  Then re-run `uv run python scripts/verify_macro_series.py`, re-record the FOMC fixtures per the note in
  `tests/fixtures/fomc/README.md`, and re-run the eval/real-run steps that depend on them.

  Everything not gated on live network access proceeds fully: config models, transforms, revisions,
  regimes, delayed-data detection, event detection, and FOMC decision/vote parsing + sentence diff are all
  pure Python, real (not placeholder) implementations, tested against fixtures. Because I can't fetch real
  FOMC statement HTML right now, the parsing/diff test fixtures are hand-written to faithfully match the
  real page structure and real statement language patterns documented in the spec, but they are
  **reconstructed, not live captures** — flagged clearly in `tests/fixtures/fomc/README.md` and must be
  swapped for real captures once network access allows it. `api.anthropic.com` is reachable, so LLM-
  dependent work is gated only on `agents-core`, not on network policy.

## Steps for the morning

TBD — filled in as the picture becomes clear.
