# fed-agent

Agents that track US macroeconomic indicators and Federal Reserve
communications, and explain what changed since the last release in plain
English — grounded entirely in numbers computed in code, never numbers
produced by a model.

The full build spec is at [`docs/specs/SPEC_MACRO.md`](docs/specs/SPEC_MACRO.md).

## Status

Following the spec's build order (§14):

- [x] **1. Guard first** — `core/guards.py` (`verify_numbers`) and the
      retry/fallback policy in `core/llm.py` (§7.4). This is shared by every
      agent added later.
- [ ] 2. Config and FRED fetch
- [ ] 3. Transforms, revisions, regimes, events
- [ ] 4. FOMC statement/minutes parsing and diff
- [ ] 5. LLM analysis (what-changed brief, FOMC read, minutes summary)
- [ ] 6. Runner integration and state
- [ ] 7. Evals
- [ ] 8. Scheduled workflow

## Development

```bash
uv sync
uv run pytest
```
