"""Record the real FRED history the release-investigator evals serve their tools from.

    uv run python scripts/record_eval_series.py      # needs FRED_API_KEY; live network

Writes evals/macro/fixtures/investigator/series.json: every configured indicator and
component series from 2015 on (daily series resampled to month-end, which is all the
investigator's tools use), keyed by FRED series id. A deliberate, separate step, like
scripts/verify_macro_series.py: the evals themselves never touch the network.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents_core import settings  # noqa: E402
from agents_core.http import Http  # noqa: E402

from agents.macro.config import load_macro_config  # noqa: E402
from agents.macro.fetch_fred import fetch_observations  # noqa: E402
from agents.macro.transform import resample_month_end  # noqa: E402

OUT = Path("evals/macro/fixtures/investigator/series.json")
START = "2015-01-01"


def main() -> int:
    settings.load_dotenv()
    api_key = settings.require_env("FRED_API_KEY")
    config = load_macro_config()
    series: dict[str, dict] = {}
    with Http() as http:
        for ind in [*config.indicators, *config.components]:
            sid = ind.fred_series
            if sid in series:
                continue
            obs = fetch_observations(http, sid, api_key, observation_start=START)
            if ind.frequency == "daily":
                obs = resample_month_end(obs)
            series[sid] = {
                "frequency": ind.frequency,
                "dates": [o.date.isoformat() for o in obs],
                "values": [o.value for o in obs],
            }
            print(f"{sid:<16} {len(obs):>5} obs, last {obs[-1].date} = {obs[-1].value}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "series": series}
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
