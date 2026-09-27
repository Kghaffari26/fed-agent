"""Verify every FRED series ID in config/macro.toml resolves and print its
metadata (title, units, frequency, last observation). Run this once during
setup, and again in CI weekly, per docs/specs/SPEC_MACRO.md §2 and §10
("series discontinued or renamed").

Usage:
    uv run python scripts/verify_macro_series.py

Requires FRED_API_KEY in the environment. Exits non-zero (and prints which
series failed) if any configured series ID can't be resolved.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from agents_core.http import HostPolicy, Http
from agents_core.settings import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.macro.config import DEFAULT_MACRO_TOML, load_macro_config  # noqa: E402
from agents.macro.fetch_fred import FRED_HOST, fetch_observations, fetch_series_meta  # noqa: E402


def main() -> int:
    load_dotenv()
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("FRED_API_KEY is not set in the environment.", file=sys.stderr)
        return 2

    config = load_macro_config(DEFAULT_MACRO_TOML)
    failures: list[str] = []

    recent = date.today().replace(year=date.today().year - 2).isoformat()
    with Http() as http:
        http.set_policy(FRED_HOST, HostPolicy(min_interval_seconds=0.5))
        for ind in [*config.indicators, *config.components]:
            try:
                meta = fetch_series_meta(http, ind.fred_series, api_key)
                obs = fetch_observations(http, ind.fred_series, api_key, observation_start=recent)
                last = next((o for o in reversed(obs) if o.value is not None), None)
                last_str = f"{last.date} = {last.value}" if last else "no non-missing observations"
                print(
                    f"OK   {ind.id:<22} {ind.fred_series:<16} "
                    f"{meta.frequency:<10} {meta.units:<30} last_updated={meta.last_updated} "
                    f"last_obs=({last_str})"
                )
            except Exception as e:  # noqa: BLE001 - report every failure, don't stop early
                failures.append(ind.id)
                print(f"FAIL {ind.id:<22} {ind.fred_series:<16} {type(e).__name__}: {e}")

    print()
    if failures:
        print(f"{len(failures)} series failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"All {len(config.indicators) + len(config.components)} configured series verified OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
