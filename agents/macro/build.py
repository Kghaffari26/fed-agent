"""Assembles the §6 blocks from computed data: indicators, regimes, yield curve,
calendar, key stats, the FOMC block, and the cross-indicator events (§5.4-§5.6).

Pure Python, no network and no LLM — `agent.MacroAgent.transform` feeds it fetched
data, and everything here is unit-tested with hand-built series.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from agents.macro.config import IndicatorConfig
from agents.macro.display import TRANSFORM_LABELS, delta_display, display_for, displayed_delta, units_display
from agents.macro.events import (
    BASE_PRIORITY,
    Event,
    delayed_event,
    fomc_decision_event,
    is_delayed,
    minutes_released_event,
    regime_change_event,
)
from agents.macro.fetch_fred import Observation
from agents.macro.fomc import Decision, ExtractedStatement, Votes, diff_statements
from agents.macro.pipeline import IndicatorSnapshot
from agents.macro.schema import (
    CalendarEntry,
    FomcChange,
    FomcLatest,
    FomcTargetRange,
    FomcVoteAgainst,
    FomcVotes,
    IndicatorOutput,
    InversionPeriod,
    KeyStat,
    Regime,
    Regimes,
    RevisionBlock,
    SeriesData,
    ValueBlock,
    YieldCurve,
    YieldCurveSeries,
    YieldSnapshotPoint,
)
from agents.macro.transform import (
    confirmed_sign_change,
    curve_last_sign_change,
    curve_regime,
    growth_regime,
    inflation_regime,
    inversion_periods,
    labor_regime,
    resample_month_end,
    resample_weekly_friday,
    rolling_mean_series,
    transform_series,
    value_on_or_before,
)

log = logging.getLogger(__name__)

SPARK_POINTS = 24
CALENDAR_HORIZON_DAYS = 31
YIELD_TENORS = [("3M", "DGS3MO"), ("2Y", "DGS2"), ("5Y", "DGS5"), ("10Y", "DGS10"), ("30Y", "DGS30")]
CURVE_SERIES = {"T10Y2Y": "10Y–2Y", "T10Y3M": "10Y–3M"}
KEY_STAT_IDS = [
    ("cpi", "CPI YoY"),
    ("core_pce", "Core PCE YoY"),
    ("unrate", "Unemployment"),
    ("treasury_10y", "10Y yield"),
]


def period_label(day: date, frequency: str) -> str:
    if frequency == "monthly":
        return day.strftime("%b %Y")
    if frequency == "quarterly":
        return f"Q{(day.month - 1) // 3 + 1} {day.year}"
    return f"{day:%b} {day.day}, {day.year}"


def years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # Feb 29
        return day.replace(year=day.year - years, day=28)


def _series_data(obs: list[Observation], rnd) -> SeriesData:
    return SeriesData(dates=[o.date for o in obs], values=[rnd(o.value) for o in obs])


# --------------------------------------------------------------------------
# Release timing: next release, delayed (§3, §5.5)
# --------------------------------------------------------------------------


@dataclass
class ReleaseTiming:
    released_at: date | None
    next_release: date | None
    delayed: bool
    last_scheduled: date | None


def release_timing(
    *, frequency: str, last_updated: date | None, release_dates: list[date], today: date
) -> ReleaseTiming:
    """`last_updated` is the date part of FRED's `last_updated`, which is when the
    latest print (or revision) landed — published as `released_at`.

    Delayed detection only applies to non-daily series: daily release calendars
    (H.15 and friends) list every business day and FRED often posts a day behind.
    """
    past = [d for d in release_dates if d <= today]
    last_scheduled = max(past) if past else None
    upcoming = [d for d in release_dates if d > today or (d == today and (last_updated or date.min) < d)]
    delayed = False
    if frequency != "daily" and last_scheduled is not None and last_updated is not None:
        delayed = is_delayed(
            scheduled_release=last_scheduled, today=today, has_new_observation=last_updated >= last_scheduled
        )
    return ReleaseTiming(
        released_at=last_updated,
        next_release=min(upcoming) if upcoming else None,
        delayed=delayed,
        last_scheduled=last_scheduled,
    )


# --------------------------------------------------------------------------
# Indicators (§6 indicators[])
# --------------------------------------------------------------------------


def build_indicator(
    ind: IndicatorConfig,
    observations: list[Observation],
    snapshot: IndicatorSnapshot,
    timing: ReleaseTiming,
    *,
    publish_years: int,
    stale: bool = False,
) -> IndicatorOutput | None:
    if snapshot.primary is None or snapshot.period is None:
        log.warning("%s: no usable observations; leaving it out of latest.json", ind.id)
        return None
    primary_disp = display_for(ind, ind.primary)
    delta_disp = delta_display(ind)
    delta = displayed_delta(ind, snapshot.primary, snapshot.prior_primary)

    observations = [o for o in observations if o.date <= snapshot.period]
    series_start = years_before(snapshot.period, publish_years)
    # The transform needs look-back before the window (13 months for YoY), so compute
    # over the full history and keep the window.
    history = transform_series(
        ind.primary, observations, ind.frequency, primary=ind.primary, since=series_start
    )
    history = [o for o in history if o.value is not None]
    if ind.frequency == "daily":
        chart = resample_weekly_friday(history)
        spark_start = years_before(snapshot.period, 2)
        spark = resample_month_end([o for o in history if o.date > spark_start])[-SPARK_POINTS:]
    elif ind.frequency == "weekly":
        chart = history
        spark_start = years_before(snapshot.period, 2)
        spark = resample_month_end([o for o in history if o.date > spark_start])[-SPARK_POINTS:]
    else:
        chart = history
        spark = history[-SPARK_POINTS:]

    revision = None
    if snapshot.revisions:
        latest_rev = snapshot.revisions[-1]
        revision = RevisionBlock(
            period_label=period_label(latest_rev.period, ind.frequency),
            old=latest_rev.old,
            new=latest_rev.new,
            format=primary_disp.format if primary_disp.format != "percent" else "percent",
        )

    return IndicatorOutput(
        id=ind.id,
        name=ind.name,
        group=ind.group,
        fred_series=ind.fred_series,
        source_url=ind.source_url,
        frequency=ind.frequency,
        units_display=units_display(ind),
        primary=ValueBlock(
            label=TRANSFORM_LABELS[ind.primary],
            value=primary_disp.round(snapshot.primary),
            format=primary_disp.format,
        ),
        change=None
        if delta is None
        else ValueBlock(
            label="vs prior", value=delta, format=delta_disp.format, good_direction=ind.good_direction
        ),
        secondary=[
            ValueBlock(
                label=TRANSFORM_LABELS[name],
                value=display_for(ind, name).round(value),
                format=display_for(ind, name).format,
            )
            for name, value in snapshot.secondary.items()
            if value is not None
        ],
        period=snapshot.period,
        period_label=period_label(snapshot.period, ind.frequency),
        released_at=timing.released_at,
        next_release=timing.next_release,
        delayed=timing.delayed,
        stale=stale,
        revision=revision,
        spark=_series_data(spark, primary_disp.round),
        series=_series_data(chart, primary_disp.round),
    )


# --------------------------------------------------------------------------
# Regimes (§5.4)
# --------------------------------------------------------------------------


@dataclass
class RegimeInputs:
    core_pce: IndicatorSnapshot | None = None
    unrate_series: list[Observation] = field(default_factory=list)
    payrolls: IndicatorSnapshot | None = None
    gdp: IndicatorSnapshot | None = None
    curve_10y2y: list[Observation] = field(default_factory=list)
    policy_decision: str | None = None
    target_range: dict[str, float] | None = None


_POLICY_LABELS = {"hold": "Holding", "cut": "Cutting", "hike": "Hiking"}


def _pct(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}%"


def build_regimes(inputs: RegimeInputs) -> Regimes:
    na = Regime(label="n/a", detail="Not enough data")

    inflation = na
    cp = inputs.core_pce
    if cp and cp.primary is not None and cp.secondary.get("ann_3m_pct") is not None:
        yoy, ann3 = round(cp.primary, 1), round(cp.secondary["ann_3m_pct"], 1)
        inflation = Regime(
            label=inflation_regime(cp.primary, cp.secondary["ann_3m_pct"]),
            detail=f"Core PCE {_pct(yoy)} YoY; {_pct(ann3)} 3-mo annualized",
        )

    labor = na
    if inputs.unrate_series:
        avg3 = rolling_mean_series(inputs.unrate_series, 3)
        payrolls_avg3 = inputs.payrolls.secondary.get("avg_3") if inputs.payrolls else None
        latest_rate = next((o.value for o in reversed(inputs.unrate_series) if o.value is not None), None)
        detail = f"Unemployment {_pct(latest_rate)}" if latest_rate is not None else "Unemployment n/a"
        if payrolls_avg3 is not None:
            detail += f"; payrolls 3-mo avg {round(payrolls_avg3):+,}K"
        labor = Regime(label=labor_regime(avg3, payrolls_avg3), detail=detail)

    growth = na
    if inputs.gdp and inputs.gdp.primary is not None and inputs.gdp.period is not None:
        gdp_value = round(inputs.gdp.primary, 1)
        growth = Regime(
            label=growth_regime(inputs.gdp.primary),
            detail=f"Real GDP {gdp_value:+.1f}% annualized ({period_label(inputs.gdp.period, 'quarterly')})",
        )

    policy = na
    if inputs.target_range:
        tr = inputs.target_range
        policy = Regime(
            label=_POLICY_LABELS.get(inputs.policy_decision or "hold", "Holding"),
            detail=f"Target range {tr['lower']:.2f}–{tr['upper']:.2f}%",
        )

    curve = na
    spread = [o for o in inputs.curve_10y2y if o.value is not None]
    if spread:
        latest = spread[-1].value
        change = curve_last_sign_change(spread)
        detail = f"10Y–2Y {latest:+.2f} pp"
        if change:
            detail += f"; last sign change {change:%b %Y}"
        curve = Regime(label=curve_regime(latest), detail=detail)

    return Regimes(inflation=inflation, labor=labor, growth=growth, policy=policy, curve=curve)


def regime_events(previous: dict[str, str], current: Regimes, *, today: date) -> list[Event]:
    """§5.6 `regime_change`, only against a previous run's labels (none on a first run)."""
    events = []
    for name in ("inflation", "labor", "growth", "policy", "curve"):
        old = previous.get(name)
        new = getattr(current, name).label
        if old is None or new == "n/a" or old == "n/a":
            continue
        event = regime_change_event(name, old_label=old, new_label=new, period=today)
        if event:
            events.append(event)
    return events


# --------------------------------------------------------------------------
# Curve sign change (§5.6) and delayed events
# --------------------------------------------------------------------------


def curve_sign_change_events(
    spreads: dict[str, list[Observation]], stored_latest: dict[str, date | None]
) -> list[Event]:
    """A sign change is surfaced once, on the run where its 5th confirming reading
    first appears (i.e. confirmed after the last date already in state)."""
    events = []
    for series_id, obs in spreads.items():
        found = confirmed_sign_change(obs)
        if not found:
            continue
        change_date, confirmed_on = found
        previous_latest = stored_latest.get(series_id)
        if previous_latest is None or confirmed_on <= previous_latest:
            continue
        new_value = next(o.value for o in obs if o.date == change_date)
        events.append(
            Event(
                id=f"curve_sign_change:{series_id}:{change_date.isoformat()}",
                type="curve_sign_change",
                priority=BASE_PRIORITY["curve_sign_change"],
                facts={
                    "curve": CURVE_SERIES.get(series_id, series_id),
                    "sign_change_date": change_date.isoformat(),
                    "new_sign": "positive" if new_value > 0 else "negative",
                    "confirmed_on": confirmed_on.isoformat(),
                },
                as_of=confirmed_on,
            )
        )
    return events


def delayed_events(
    timings: dict[str, ReleaseTiming], previously_delayed: set[str], names: dict[str, str]
) -> list[Event]:
    """One event when an indicator *becomes* delayed, not on every run it stays so."""
    events = []
    for indicator_id, timing in timings.items():
        if timing.delayed and indicator_id not in previously_delayed and timing.last_scheduled:
            event = delayed_event(indicator_id, scheduled_release=timing.last_scheduled)
            event.facts = {"indicator": names.get(indicator_id, indicator_id), **event.facts}
            events.append(event)
    return events


# --------------------------------------------------------------------------
# Yield curve (§6 yield_curve)
# --------------------------------------------------------------------------


def build_yield_curve(obs: dict[str, list[Observation]], *, publish_years: int) -> YieldCurve | None:
    y2, y10, spread = obs.get("DGS2", []), obs.get("DGS10", []), obs.get("T10Y2Y", [])
    if not (y2 and y10 and spread):
        return None
    latest = max(s[-1].date for s in (y2, y10, spread))
    start = years_before(latest, publish_years)

    def weekly(series: list[Observation]) -> dict[date, float]:
        return {o.date: o.value for o in resample_weekly_friday([o for o in series if o.date >= start])}

    w2, w10, ws = weekly(y2), weekly(y10), weekly(spread)
    dates = sorted(set(w2) | set(w10) | set(ws))

    def col(values: dict[date, float]) -> list[float | None]:
        return [None if values.get(d) is None else round(values[d], 2) for d in dates]

    periods = [
        InversionPeriod(start=s, end=e) for s, e in inversion_periods([o for o in spread if o.date >= start])
    ]
    snapshot = []
    for tenor, series_id in YIELD_TENORS:
        last = next((o for o in reversed(obs.get(series_id, [])) if o.value is not None), None)
        if last is not None:
            snapshot.append(YieldSnapshotPoint(tenor=tenor, value=round(last.value, 2)))
    return YieldCurve(
        series=YieldCurveSeries(dates=dates, y2=col(w2), y10=col(w10), spread_10y2y=col(ws)),
        inversion_periods=periods,
        snapshot=snapshot,
    )


# --------------------------------------------------------------------------
# Calendar (§6 calendar)
# --------------------------------------------------------------------------


def build_calendar(
    releases: dict[str, tuple[str, list[date], list[str]]],
    *,
    today: date,
    horizon_days: int = CALENDAR_HORIZON_DAYS,
) -> list[CalendarEntry]:
    """`releases` maps release_id -> (release name, scheduled dates, indicator ids)."""
    end = today + timedelta(days=horizon_days)
    entries = []
    for name, dates, indicator_ids in releases.values():
        for d in dates:
            if today <= d <= end:
                entries.append(CalendarEntry(date=d, release=name, indicator_ids=sorted(indicator_ids)))
    entries.sort(key=lambda e: (e.date, e.release))
    return entries


# --------------------------------------------------------------------------
# Key stats (§6 key_stats, also manifest-entry.json: at most 4)
# --------------------------------------------------------------------------


def build_key_stats(indicators: list[IndicatorOutput]) -> list[KeyStat]:
    by_id = {i.id: i for i in indicators}
    stats = []
    for indicator_id, label in KEY_STAT_IDS:
        ind = by_id.get(indicator_id)
        if ind is None:
            continue
        stats.append(
            KeyStat(
                label=label,
                value=ind.primary.value,
                format=ind.primary.format,
                delta=ind.change.value if ind.change else None,
                delta_format=ind.change.format if ind.change else None,
                good_direction=ind.change.good_direction
                if ind.change and ind.change.good_direction
                else "neutral",
            )
        )
    return stats


# --------------------------------------------------------------------------
# FOMC block (§5.7, §6 fomc.latest)
# --------------------------------------------------------------------------


@dataclass
class StatementInput:
    date: date
    url: str
    extracted: ExtractedStatement
    decision: Decision
    votes: Votes


def build_fomc_latest(latest: StatementInput, previous: StatementInput | None) -> FomcLatest:
    """The statement block before the LLM read is attached. change_bp is against the
    previous statement's range when we have it (§5.7 step 3)."""
    change_bp = latest.decision.change_bp
    if previous is not None:
        change_bp = round(
            (latest.decision.target_range["lower"] - previous.decision.target_range["lower"]) * 100
        )
    changes = (
        diff_statements(previous.extracted.policy_text, latest.extracted.policy_text) if previous else []
    )
    return FomcLatest(
        date=latest.date,
        url=latest.url,
        decision=latest.decision.decision,
        target_range=FomcTargetRange(**latest.decision.target_range),
        change_bp=change_bp,
        votes=FomcVotes(
            for_count=latest.votes.for_count,
            against=[FomcVoteAgainst(**d) for d in latest.votes.against],
        ),
        latest_text=latest.extracted.policy_text,
        previous_date=previous.date if previous else None,
        previous_text=previous.extracted.policy_text if previous else None,
        changes=[FomcChange(idx=c.idx, type=c.type, before=c.before, after=c.after) for c in changes],
        read=None,
    )


def crosscheck_target_range(
    block: FomcLatest, upper: list[Observation], lower: list[Observation]
) -> tuple[FomcLatest, str | None]:
    """§5.7 step 3: once FRED's DFEDTARU/L have a reading after the statement date,
    compare. On a mismatch, warn and trust FRED. Until then, crosscheck_pending."""
    after = block.date + timedelta(days=1)
    u = next((o for o in upper if o.date >= after and o.value is not None), None)
    lo = next((o for o in lower if o.date >= after and o.value is not None), None)
    if u is None or lo is None:
        return block.model_copy(update={"crosscheck_pending": True}), None
    fred = FomcTargetRange(lower=lo.value, upper=u.value)
    if fred != block.target_range:
        warning = (
            f"FOMC {block.date}: parsed target range {block.target_range.lower}-{block.target_range.upper} "
            f"disagrees with FRED {fred.lower}-{fred.upper}; using FRED"
        )
        return block.model_copy(update={"target_range": fred, "crosscheck_pending": False}), warning
    return block.model_copy(update={"crosscheck_pending": False}), None


def fomc_events(block: FomcLatest | None, *, is_new: bool) -> list[Event]:
    if block is None or not is_new:
        return []
    event = fomc_decision_event(
        statement_date=block.date,
        decision=block.decision,
        target_range={"lower": block.target_range.lower, "upper": block.target_range.upper},
    )
    event.facts["change_bp"] = block.change_bp
    event.facts["dissents"] = len(block.votes.against)
    return [event]


def minutes_events(meeting_date: date | None, released_at: date | None, *, is_new: bool) -> list[Event]:
    if not is_new or meeting_date is None or released_at is None:
        return []
    event = minutes_released_event(meeting_date=meeting_date, released_at=released_at)
    event.facts["released_at"] = released_at.isoformat()
    return [event]


def target_range_from_fred(upper: list[Observation], lower: list[Observation]) -> dict[str, float] | None:
    u = value_on_or_before(upper, upper[-1].date) if upper else None
    lo = value_on_or_before(lower, lower[-1].date) if lower else None
    if u is None or lo is None:
        return None
    return {"lower": lo.value, "upper": u.value}
