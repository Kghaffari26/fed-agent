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
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.macro.config import DEFAULT_MACRO_TOML, load_macro_config  # noqa: E402
from agents.macro.fetch_fred import fetch_observations, fetch_series_meta  # noqa: E402


def main() -> int:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("FRED_API_KEY is not set in the environment.", file=sys.stderr)
        return 2

    config = load_macro_config(DEFAULT_MACRO_TOML)
    failures: list[str] = []

    with httpx.Client(timeout=15.0) as client:
        for ind in config.indicators:
            try:
                meta = fetch_series_meta(client.get, ind.fred_series, api_key)
                obs = fetch_observations(client.get, ind.fred_series, api_key)
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
    print(f"All {len(config.indicators)} configured series verified OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
