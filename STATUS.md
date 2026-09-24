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

## Steps for the morning

TBD — filled in as the picture becomes clear.
