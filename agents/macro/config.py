"""Loads config/macro.toml and config/fomc_dates.toml into pydantic models.

See docs/specs/SPEC_MACRO.md §2 and §8.
"""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Frequency = Literal["daily", "weekly", "monthly", "quarterly"]
GoodDirection = Literal["up", "down", "neutral"]
Transform = Literal[
    "yoy_pct", "mom_pct", "mom_diff", "ann_3m_pct", "avg_3", "avg_4w", "change_pp", "level"
]
UnitsScale = Literal["thousands", "millions", "billions"]

DEFAULT_MACRO_TOML = Path("config/macro.toml")
DEFAULT_FOMC_DATES_TOML = Path("config/fomc_dates.toml")


class Settings(BaseModel):
    first_run_years: int = 11
    publish_series_years: int = 10
    summarize_minutes: bool = True
    use_bls_fallback: bool = False
    max_events_to_llm: int = 8


class IndicatorConfig(BaseModel):
    id: str
    name: str
    group: str
    fred_series: str
    frequency: Frequency
    primary: Transform
    secondary: list[Transform] = Field(default_factory=list)
    units_scale: UnitsScale | None = None
    good_direction: GoodDirection
    high_priority: bool = False
    thresholds: list[float] = Field(default_factory=list)


class MacroConfig(BaseModel):
    settings: Settings
    indicators: list[IndicatorConfig]

    def indicator(self, indicator_id: str) -> IndicatorConfig:
        for ind in self.indicators:
            if ind.id == indicator_id:
                return ind
        raise KeyError(f"no indicator configured with id={indicator_id!r}")

    def indicators_by_group(self, group: str) -> list[IndicatorConfig]:
        return [ind for ind in self.indicators if ind.group == group]


class FomcMeeting(BaseModel):
    start: date
    end: date
    sep: bool = False

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, end: date, info) -> date:
        start = info.data.get("start")
        if start is not None and end < start:
            raise ValueError(f"meeting end {end} is before start {start}")
        return end


class FomcCalendar(BaseModel):
    meetings: list[FomcMeeting]

    def next_meeting(self, as_of: date) -> FomcMeeting | None:
        upcoming = [m for m in self.meetings if m.end >= as_of]
        return min(upcoming, key=lambda m: m.start) if upcoming else None

    def is_decision_day(self, day: date) -> bool:
        """True if `day` is the last day of an FOMC meeting (statement day)."""
        return any(m.end == day for m in self.meetings)


def load_macro_config(path: Path = DEFAULT_MACRO_TOML) -> MacroConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return MacroConfig(
        settings=Settings(**raw.get("settings", {})),
        indicators=raw.get("indicator", []),
    )


def load_fomc_calendar(path: Path = DEFAULT_FOMC_DATES_TOML) -> FomcCalendar:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return FomcCalendar(meetings=raw.get("meeting", []))
