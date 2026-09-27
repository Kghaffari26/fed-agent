"""Pydantic output models — the site contract (§6).

`MacroOutput` is the source of truth for `latest.json`. It subclasses
`agents_core.schema.AgentOutput`, so `meta` is agents-core's shared `RunMeta` (the
SPEC_WEBSITE §3 meta block: run_id, status, data_changed, cost, sources...), filled in
by the agents-core runner, and `key_stats` uses agents-core's `KeyStat` (the same
model as `manifest-entry.json`). Every other block keeps SPEC_MACRO.md §6's shape.

The runner publishes `schema.json` next to `latest.json` automatically; the
committed `schemas/macro.schema.json` snapshot is regenerated with
`uv run python scripts/export_schema.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from agents_core.schema import AgentOutput, KeyStat, StatFormat, Timestamp
from pydantic import BaseModel, Field

NarrativeSource = Literal["llm", "template"]
ToneShift = Literal["more_hawkish", "unchanged", "more_dovish"]
FomcDecisionKind = Literal["hold", "cut", "hike"]
ChangeType = Literal["added", "removed", "modified"]
GoodDirection = Literal["up", "down", "neutral"]
RegimeLabel = str  # see agents.macro.transform for the literal labels per regime

__all__ = ["KeyStat", "MacroOutput"]


class Regime(BaseModel):
    label: RegimeLabel
    detail: str


class Regimes(BaseModel):
    inflation: Regime
    labor: Regime
    growth: Regime
    policy: Regime
    curve: Regime


class Citation(BaseModel):
    name: str
    url: str


class BriefBullet(BaseModel):
    text: str
    event_ids: list[str]
    citations: list[Citation] = Field(default_factory=list)


class Brief(BaseModel):
    bullets: list[BriefBullet]
    narrative_source: NarrativeSource
    model: str | None = None
    generated_at: Timestamp
    reused_from_run_id: str | None = None


class ValueBlock(BaseModel):
    label: str
    value: float
    # agents-core's standard formats only (the site renders exactly these).
    format: StatFormat
    delta: float | None = None
    delta_format: StatFormat | None = None
    good_direction: GoodDirection | None = None


class RevisionBlock(BaseModel):
    period_label: str
    old: float
    new: float
    format: StatFormat


class SeriesData(BaseModel):
    dates: list[date]
    values: list[float | None]


class IndicatorOutput(BaseModel):
    id: str
    name: str
    group: str
    fred_series: str
    source_url: str
    frequency: str
    units_display: str
    primary: ValueBlock
    change: ValueBlock | None = None
    secondary: list[ValueBlock] = Field(default_factory=list)
    period: date
    period_label: str
    released_at: date | None = None
    next_release: date | None = None
    delayed: bool = False
    # §10: FRED failed for this series this run; the values are the last good ones.
    stale: bool = False
    revision: RevisionBlock | None = None
    spark: SeriesData
    series: SeriesData


class YieldCurveSeries(BaseModel):
    dates: list[date]
    y2: list[float | None]
    y10: list[float | None]
    spread_10y2y: list[float | None]


class InversionPeriod(BaseModel):
    start: date
    end: date | None = None


class YieldSnapshotPoint(BaseModel):
    tenor: str
    value: float


class YieldCurve(BaseModel):
    series: YieldCurveSeries
    inversion_periods: list[InversionPeriod] = Field(default_factory=list)
    snapshot: list[YieldSnapshotPoint]


class FomcVoteAgainst(BaseModel):
    name: str
    preferred: str


class FomcVotes(BaseModel):
    for_count: int
    against: list[FomcVoteAgainst] = Field(default_factory=list)


class FomcTargetRange(BaseModel):
    lower: float
    upper: float


class FomcChange(BaseModel):
    idx: int
    type: ChangeType
    before: str | None = None
    after: str | None = None


class FomcKeyPhrase(BaseModel):
    phrase: str
    interpretation: str


class FomcRead(BaseModel):
    summary: str
    tone_shift: ToneShift
    rationale: str
    cited_change_idx: list[int] = Field(default_factory=list)
    key_phrases: list[FomcKeyPhrase] = Field(default_factory=list)
    narrative_source: NarrativeSource


class FomcLatest(BaseModel):
    date: date
    url: str
    decision: FomcDecisionKind
    target_range: FomcTargetRange
    change_bp: int | None = None
    votes: FomcVotes
    latest_text: str
    previous_date: date | None = None
    previous_text: str | None = None
    changes: list[FomcChange] = Field(default_factory=list)
    read: FomcRead | None = None
    crosscheck_pending: bool = False


class FomcNextMeeting(BaseModel):
    start: date
    end: date
    has_sep: bool = False


class FomcMinutesOut(BaseModel):
    meeting_date: date
    released_at: date
    url: str
    summary: str | None = None
    narrative_source: NarrativeSource | None = None


class FomcBlock(BaseModel):
    latest: FomcLatest | None = None
    next_meeting: FomcNextMeeting | None = None
    minutes: FomcMinutesOut | None = None


class CalendarEntry(BaseModel):
    date: date
    release: str
    indicator_ids: list[str]


class EventOut(BaseModel):
    id: str
    type: str
    priority: int
    facts: dict = Field(default_factory=dict)


# ---- §6.1 investigation (additive; agents-core v0.3.0 agent loop) ------------------------


class InvestigationTrigger(BaseModel):
    event_id: str
    type: str
    indicator_id: str | None = None


class CitedSeries(BaseModel):
    id: str
    name: str
    fred_series: str
    url: str


class InvestigationLoop(BaseModel):
    """What the agent loop did. Null when no loop ran (no API key)."""

    steps: int
    tool_calls: list[str] = Field(default_factory=list)
    stop_reason: str
    cost_usd: float
    guard_attempts: int = 0


class Investigation(BaseModel):
    trigger: InvestigationTrigger
    analysis: str
    cited_series: list[CitedSeries] = Field(default_factory=list)
    narrative_source: NarrativeSource
    model: str | None = None
    generated_at: Timestamp
    reused_from_run_id: str | None = None
    loop: InvestigationLoop | None = None


class MacroOutput(AgentOutput):
    headline: str
    key_stats: list[KeyStat]
    regimes: Regimes
    brief: Brief
    indicators: list[IndicatorOutput]
    yield_curve: YieldCurve
    fomc: FomcBlock
    calendar: list[CalendarEntry] = Field(default_factory=list)
    events: list[EventOut] = Field(default_factory=list)
    # §6.1, added in schema 1.1.0: the release investigator's latest analysis, or null
    # before the first high-priority release.
    investigation: Investigation | None = None
