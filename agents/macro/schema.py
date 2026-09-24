"""Pydantic output models — the site contract (§6).

This is the source of truth for `site/public/data/macro/latest.json`,
exported to `schemas/macro.schema.json` by `scripts/export_schema.py`.

`Meta` is a minimal stand-in for the shared meta block described in
SPEC_WEBSITE.md §3 (a spec for the website repo, not available here — see
DECISIONS.md). Once agents_core.schema provides the real shared `RunMeta`,
swap this for it; the field names below were chosen to match what §6 and
§10 of SPEC_MACRO.md reference directly (`meta.data_changed`,
`meta.status`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "failed"]
NarrativeSource = Literal["llm", "template"]
ToneShift = Literal["more_hawkish", "unchanged", "more_dovish"]
FomcDecisionKind = Literal["hold", "cut", "hike"]
ChangeType = Literal["added", "removed", "modified"]
GoodDirection = Literal["up", "down", "neutral"]
RegimeLabel = str  # see agents.macro.transform for the literal labels per regime


class Meta(BaseModel):
    run_id: str
    generated_at: datetime
    status: Status
    data_changed: bool
    warnings: list[str] = Field(default_factory=list)


class KeyStat(BaseModel):
    label: str
    value: float
    format: str
    delta: float | None = None
    delta_format: str | None = None
    good_direction: GoodDirection


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
    generated_at: datetime
    reused_from_run_id: str | None = None


class ValueBlock(BaseModel):
    label: str
    value: float
    format: str
    delta: float | None = None
    delta_format: str | None = None
    good_direction: GoodDirection | None = None


class RevisionBlock(BaseModel):
    period_label: str
    old: float
    new: float
    format: str


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


class MacroOutput(BaseModel):
    meta: Meta
    headline: str
    key_stats: list[KeyStat]
    regimes: Regimes
    brief: Brief
    indicators: list[IndicatorOutput]
    yield_curve: YieldCurve
    fomc: FomcBlock
    calendar: list[CalendarEntry] = Field(default_factory=list)
    events: list[EventOut] = Field(default_factory=list)
