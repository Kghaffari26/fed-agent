"""Read/write data/macro/state.json (§4). Committed, not published to the site.

The §4 fields (per-series `last_updated` + last 36 observations, FOMC dates, the
last brief) plus regime labels and delayed flags, so `regime_change`/`delayed`
events fire once, on the transition.

Everything else a no-change run republishes — the FOMC block and minutes block with
their LLM reads, the headline, the investigation — comes from the previous
`latest.json` (`ctx.previous_latest()`), which agents-core's run-agent.yml restores
from the `data` branch since v0.2.0. `FomcState.latest`/`.minutes` and
`LastBrief.headline` are read from an older state.json only as a one-time migration
fallback (when there is no previous latest.json yet) and are no longer written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.macro.fetch_fred import Observation

MAX_STORED_OBSERVATIONS = 36
DEFAULT_STATE_PATH = Path("data/macro/state.json")


@dataclass
class SeriesState:
    last_updated: str | None = None
    observations: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"last_updated": self.last_updated, "observations": self.observations}

    @classmethod
    def from_dict(cls, raw: dict) -> SeriesState:
        return cls(last_updated=raw.get("last_updated"), observations=raw.get("observations", {}))


@dataclass
class FomcState:
    latest_statement_date: str | None = None
    latest_minutes_date: str | None = None
    # Legacy (read-only): the `fomc.latest` / `fomc.minutes` blocks older versions kept
    # here because CI never restored public-data/. Only a migration fallback now.
    latest: dict[str, Any] | None = None
    minutes: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return {
            "latest_statement_date": self.latest_statement_date,
            "latest_minutes_date": self.latest_minutes_date,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> FomcState:
        return cls(
            latest_statement_date=raw.get("latest_statement_date"),
            latest_minutes_date=raw.get("latest_minutes_date"),
            latest=raw.get("latest"),
            minutes=raw.get("minutes"),
        )


@dataclass
class LastBrief:
    run_id: str
    bullets: list[dict[str, Any]]  # schema.BriefBullet as JSON
    event_ids: list[str]
    narrative_source: str = "template"
    model: str | None = None
    generated_at: str | None = None
    headline: str | None = None  # legacy, read-only (see the module docstring)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "bullets": self.bullets,
            "event_ids": self.event_ids,
            "narrative_source": self.narrative_source,
            "model": self.model,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> LastBrief:
        bullets = [b if isinstance(b, dict) else {"text": b, "event_ids": []} for b in raw.get("bullets", [])]
        return cls(
            run_id=raw["run_id"],
            bullets=bullets,
            event_ids=raw.get("event_ids", []),
            narrative_source=raw.get("narrative_source", "template"),
            model=raw.get("model"),
            generated_at=raw.get("generated_at"),
            headline=raw.get("headline"),
        )


@dataclass
class MacroState:
    series: dict[str, SeriesState] = field(default_factory=dict)
    fomc: FomcState = field(default_factory=FomcState)
    last_brief: LastBrief | None = None
    regimes: dict[str, str] = field(default_factory=dict)
    delayed: list[str] = field(default_factory=list)  # indicator ids flagged delayed last run

    def to_dict(self) -> dict:
        return {
            "series": {sid: s.to_dict() for sid, s in sorted(self.series.items())},
            "fomc": self.fomc.to_dict(),
            "last_brief": self.last_brief.to_dict() if self.last_brief else None,
            "regimes": dict(sorted(self.regimes.items())),
            "delayed": sorted(self.delayed),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> MacroState:
        series = {sid: SeriesState.from_dict(v) for sid, v in raw.get("series", {}).items()}
        fomc = FomcState.from_dict(raw.get("fomc") or {})
        last_brief_raw = raw.get("last_brief")
        last_brief = LastBrief.from_dict(last_brief_raw) if last_brief_raw else None
        return cls(
            series=series,
            fomc=fomc,
            last_brief=last_brief,
            regimes=dict(raw.get("regimes") or {}),
            delayed=list(raw.get("delayed") or []),
        )

    def series_last_updated(self, series_id: str) -> str | None:
        stored = self.series.get(series_id)
        return stored.last_updated if stored else None


def load_state(path: Path = DEFAULT_STATE_PATH) -> MacroState:
    if not path.exists():
        return MacroState()
    return MacroState.from_dict(json.loads(path.read_text()))


def save_state(state: MacroState, path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(path)


def trim_observations(observations: dict[str, float | None], *, keep: int = MAX_STORED_OBSERVATIONS) -> dict:
    """Keep only the most recent `keep` dates (§4: 36 is enough for revision checks)."""
    if len(observations) <= keep:
        return dict(observations)
    ordered_dates = sorted(observations.keys())[-keep:]
    return {d: observations[d] for d in ordered_dates}


def update_series_state(
    state: MacroState, series_id: str, *, last_updated: str, new_observations: list[Observation]
) -> None:
    """Merge freshly fetched observations into stored state, keeping only the
    trailing MAX_STORED_OBSERVATIONS dates."""
    existing = state.series.get(series_id, SeriesState())
    merged = dict(existing.observations)
    for obs in new_observations:
        merged[obs.date.isoformat()] = obs.value
    state.series[series_id] = SeriesState(last_updated=last_updated, observations=trim_observations(merged))
