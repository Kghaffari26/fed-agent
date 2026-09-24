"""Read/write data/macro/state.json (§4). Committed, not published to the site."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

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
        )


@dataclass
class LastBrief:
    run_id: str
    bullets: list[str]
    event_ids: list[str]

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "bullets": self.bullets, "event_ids": self.event_ids}

    @classmethod
    def from_dict(cls, raw: dict) -> LastBrief:
        return cls(run_id=raw["run_id"], bullets=raw.get("bullets", []), event_ids=raw.get("event_ids", []))


@dataclass
class MacroState:
    series: dict[str, SeriesState] = field(default_factory=dict)
    fomc: FomcState = field(default_factory=FomcState)
    last_brief: LastBrief | None = None

    def to_dict(self) -> dict:
        return {
            "series": {sid: s.to_dict() for sid, s in self.series.items()},
            "fomc": self.fomc.to_dict(),
            "last_brief": self.last_brief.to_dict() if self.last_brief else None,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> MacroState:
        series = {sid: SeriesState.from_dict(v) for sid, v in raw.get("series", {}).items()}
        fomc = FomcState.from_dict(raw.get("fomc", {}))
        last_brief_raw = raw.get("last_brief")
        last_brief = LastBrief.from_dict(last_brief_raw) if last_brief_raw else None
        return cls(series=series, fomc=fomc, last_brief=last_brief)

    def series_last_updated(self, series_id: str) -> str | None:
        stored = self.series.get(series_id)
        return stored.last_updated if stored else None


def load_state(path: Path = DEFAULT_STATE_PATH) -> MacroState:
    if not path.exists():
        return MacroState()
    return MacroState.from_dict(json.loads(path.read_text()))


def save_state(state: MacroState, path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n")


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
