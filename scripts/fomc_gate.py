"""Gate for .github/workflows/agent-macro.yml (SPEC_MACRO.md §9).

The FOMC-afternoon cron (19:30 UTC on weekdays) must exit immediately, with no
fetch and no LLM call, unless today is an FOMC decision day (a meeting's final
day in config/fomc_dates.toml). Every other trigger (the morning cron,
workflow_dispatch) always runs.

Standard library only, so the gate job doesn't need `uv sync`. Prints
`run=true` or `run=false` for $GITHUB_OUTPUT.

Usage:
    SCHEDULE="30 19 * * 1-5" python3 scripts/fomc_gate.py [YYYY-MM-DD]
"""

from __future__ import annotations

import os
import sys
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

FOMC_AFTERNOON_CRON = "30 19 * * 1-5"
FOMC_DATES = Path(__file__).resolve().parent.parent / "config" / "fomc_dates.toml"


def decision_days(path: Path = FOMC_DATES) -> set[date]:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return {date.fromisoformat(m["end"]) for m in raw.get("meeting", [])}


def should_run(schedule: str, today: date, path: Path = FOMC_DATES) -> bool:
    if schedule.strip() != FOMC_AFTERNOON_CRON:
        return True
    return today in decision_days(path)


def main(argv: list[str]) -> int:
    # 19:30 UTC is 14:30/15:30 in Washington: the UTC date is the ET date.
    today = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(UTC).date()
    run = should_run(os.environ.get("SCHEDULE", ""), today)
    print(f"run={'true' if run else 'false'}")
    if not run:
        print(f"{today} is not an FOMC decision day; skipping the afternoon run", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
