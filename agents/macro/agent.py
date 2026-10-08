"""The macro agent: registered with agents-core via the `agents_core.agents` entry point
(`macro = "agents.macro.agent:AGENT"` in pyproject.toml), so `uv run agents-run macro
[--dry-run]` runs it.

    fetch      FRED metadata (change detection) -> observations for changed series,
               release calendars, the Fed RSS feed, new statement/minutes pages,
               the FOMC calendar. All through ctx.http. No LLM.
    transform  pure Python: transforms, revisions, regimes, events, every §6 block
               except the LLM narrative. `--dry-run` stops here.
    analyze    the three guarded LLM calls (only for what's new), then state.json.

A run with no events makes zero LLM calls: the brief, FOMC read and minutes summary
are reused from data/macro/state.json and `data_changed` is false (§6, §10, §13).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

from agents_core import settings, tracing
from agents_core.agent import Agent, AgentResult, RunContext
from agents_core.http import HostPolicy, Http, HttpError
from agents_core.llm import LLM, tier_config
from agents_core.schema import Source

from agents.macro.analyze import (
    TIER,
    WhatChangedInput,
    generate_brief,
    generate_fomc_read,
    generate_minutes_summary,
    llm_key_problem,
)
from agents.macro.build import (
    CURVE_SERIES,
    RegimeInputs,
    ReleaseTiming,
    StatementInput,
    build_calendar,
    build_fomc_latest,
    build_indicator,
    build_key_stats,
    build_regimes,
    build_yield_curve,
    crosscheck_target_range,
    curve_sign_change_events,
    delayed_events,
    fomc_events,
    minutes_events,
    regime_events,
    release_timing,
    target_range_from_fred,
)
from agents.macro.config import (
    DEFAULT_FOMC_DATES_TOML,
    DEFAULT_MACRO_TOML,
    FomcMeeting,
    MacroConfig,
    load_fomc_calendar,
    load_macro_config,
)
from agents.macro.events import Event, rank_events
from agents.macro.fetch_fed import (
    FED_HOST,
    MinutesItem,
    compare_calendars,
    fetch_calendar,
    fetch_feed,
    fetch_page,
    minutes_items,
    parse_statement_date_from_url,
    statement_items,
)
from agents.macro.fetch_fred import (
    FRED_HOST,
    Observation,
    fetch_observations,
    fetch_release,
    fetch_release_dates,
    fetch_series_meta,
    has_series_changed,
)
from agents.macro.fomc import (
    Decision,
    ExtractedStatement,
    Votes,
    extract_minutes,
    extract_statement,
    is_extraction_valid,
    parse_decision,
    parse_votes,
)
from agents.macro.investigate import (
    Investigation as InvestigationResult,
)
from agents.macro.investigate import (
    InvestigatorData,
    SeriesSource,
    Trigger,
    fomc_context,
    investigate,
    pick_trigger,
)
from agents.macro.pipeline import IndicatorSnapshot, compute_indicator_snapshot
from agents.macro.schema import (
    Brief,
    BriefBullet,
    CitedSeries,
    EventOut,
    FomcBlock,
    FomcLatest,
    FomcMinutesOut,
    FomcNextMeeting,
    IndicatorOutput,
    Investigation,
    InvestigationLoop,
    InvestigationTrigger,
    MacroOutput,
)
from agents.macro.state import (
    LastBrief,
    MacroState,
    SeriesState,
    load_state,
    save_state,
    trim_observations,
)
from agents.macro.templates import headline_for_event, quiet_headline

log = logging.getLogger("agents.macro")

DEFAULT_OBS_CACHE_DIR = Path(".cache/macro/observations")
LATER_RUN_LOOKBACK_DAYS = 400  # §3: later runs fetch only the last 400 days
RELEASE_DATES_LOOKBACK_DAYS = 45  # enough to see the most recent past release (§5.5)
MAX_FAILED_SERIES_SHARE = 0.5  # §10: more than half failing fails the run
# FRED answers 400 ("api_key is not registered"/"not set") to every request when the
# key is bad. After this many series in a row fail that way, with none succeeding,
# stop: the key is the problem, so the rest would fail the same way.
KEY_REJECTED_AFTER = 3
FRED_KEY_ALERT = "macro: FRED rejected FRED_API_KEY"
FRED_SOURCE_URL = "https://fred.stlouisfed.org/"
FED_SOURCE_URL = "https://www.federalreserve.gov/monetarypolicy.htm"


def state_path() -> Path:
    return settings.data_dir() / "macro" / "state.json"


# ---- full observation history cache (§4: "a cached copy under data/cache/") --------


def obs_cache_dir() -> Path:
    """Full observation history per series. Outside data/ (it's large and not state);
    CI keeps it between runs with actions/cache (see agent-macro.yml)."""
    value = os.environ.get("MACRO_OBS_CACHE_DIR")
    return Path(value) if value else DEFAULT_OBS_CACHE_DIR


def fed_cache_dir() -> Path:
    """Conditional-GET copies of the Fed RSS feed and calendar page (next to the
    observation cache, so CI's actions/cache keeps their ETags too)."""
    return obs_cache_dir().parent / "fed"


def _cache_file(series_id: str) -> Path:
    return obs_cache_dir() / f"{series_id}.json"


def load_cached_observations(series_id: str) -> list[Observation] | None:
    path = _cache_file(series_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
        return [
            Observation(date=date.fromisoformat(d), value=v)
            for d, v in zip(raw["dates"], raw["values"], strict=True)
        ]
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def save_cached_observations(series_id: str, observations: list[Observation]) -> None:
    path = _cache_file(series_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"dates": [o.date.isoformat() for o in observations], "values": [o.value for o in observations]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.replace(path)


def merge_observations(old: list[Observation] | None, new: list[Observation]) -> list[Observation]:
    """New values win on overlapping dates (that is how revisions come in)."""
    merged = {o.date: o.value for o in old or []}
    merged.update({o.date: o.value for o in new})
    return [Observation(date=d, value=v) for d, v in sorted(merged.items())]


# ---- fetch output -----------------------------------------------------------------------


@dataclass
class SeriesFetch:
    last_updated: str | None
    observations: list[Observation]
    updated: bool  # FRED last_updated moved since the last run -> events allowed
    stale: bool = False


@dataclass
class StatementFetch:
    latest: StatementInput
    previous: StatementInput | None


@dataclass
class MinutesFetch:
    item: MinutesItem
    text: str


@dataclass
class ReleaseRef:
    release_id: str
    name: str


@dataclass
class RawData:
    today: date
    retrieved_at: datetime
    config: MacroConfig
    meetings: list[FomcMeeting]
    state: MacroState
    series: dict[str, SeriesFetch]
    releases: dict[str, ReleaseRef]  # by FRED series id
    release_dates: dict[str, list[date]]  # by release id: FRED's full calendar
    published_dates: dict[str, list[date]]  # by release id: dates FRED actually published
    statement: StatementFetch | None
    minutes: MinutesFetch | None
    # Release components (CPI components, payrolls by sector) for the investigator.
    components: dict[str, SeriesFetch] = field(default_factory=dict)
    # The previous latest.json (restored from the data branch in CI), or None.
    previous: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class MacroData:
    raw: RawData
    indicators: list[IndicatorOutput]
    snapshots: dict[str, IndicatorSnapshot]
    events: list[Event]
    headline: str
    body: dict[str, Any]  # every MacroOutput field except meta and brief
    fomc_latest: FomcLatest | None
    fomc_is_new: bool
    minutes: FomcMinutesOut | None
    minutes_is_new: bool
    new_state: MacroState
    stale: bool
    investigator: InvestigatorData | None = None
    trigger: Trigger | None = None


class MacroAgent(Agent):
    id = "macro"
    name = "Macro & Fed"
    route = "/macro"
    # 1.1.0: meta.warnings/meta_schema_version (agents-core v0.2.0), `investigation` (§6.1),
    # formats typed as agents-core StatFormats. Additive.
    schema_version = "1.1.0"
    expected_interval_hours = 24
    next_run_hint = "Weekdays ~7:00 PT"
    history_keep = 90
    output_model = MacroOutput

    def configure_http(self, http: Http) -> None:
        # §3: FRED allows ~120 req/min (2 req/s here); the Fed site gets at most 1 req/s.
        http.set_policy(FRED_HOST, HostPolicy(min_interval_seconds=0.5))
        http.set_policy(FED_HOST, HostPolicy(min_interval_seconds=1.0))

    # ---- fetch ------------------------------------------------------------------------

    def fetch(self, ctx: RunContext) -> RawData:
        config = load_macro_config(DEFAULT_MACRO_TOML)
        state = load_state(state_path())
        api_key = settings.require_env("FRED_API_KEY")
        today = ctx.started_at.astimezone(UTC).date()
        warnings: list[str] = []

        series: dict[str, SeriesFetch] = {}
        failed: list[str] = []
        rejected = 0  # consecutive HTTP 400s before any series succeeded
        for ind in config.indicators:
            sid = ind.fred_series
            if sid in series:
                continue
            try:
                series[sid] = self._fetch_series(ctx.http, sid, api_key, state, config, today)
                rejected = -1  # a success: the key works
            except HttpError as e:
                if rejected >= 0 and e.status == 400:
                    rejected += 1
                    if rejected >= KEY_REJECTED_AFTER:
                        self._fred_key_rejected(ctx, failed + [sid])
                cached = load_cached_observations(sid)
                failed.append(sid)
                log.warning(
                    "FRED %s failed (%s); %s", sid, e, "using cached values" if cached else "no cache"
                )
                if cached:
                    series[sid] = SeriesFetch(
                        state.series_last_updated(sid), cached, updated=False, stale=True
                    )
        if len(failed) > MAX_FAILED_SERIES_SHARE * len(config.indicators):
            raise RuntimeError(f"{len(failed)} of {len(config.indicators)} FRED series failed: {failed}")
        if failed:  # §10: publish the rest, `status: ok` with a warning
            warnings.append(f"FRED failed for {', '.join(failed)}; showing their last good values (stale)")

        # Components only feed the release investigator: a failure is a warning.
        components: dict[str, SeriesFetch] = {}
        for comp in config.components:
            try:
                components[comp.fred_series] = self._fetch_series(
                    ctx.http, comp.fred_series, api_key, state, config, today
                )
            except HttpError as e:
                cached = load_cached_observations(comp.fred_series)
                warnings.append(f"FRED failed for component {comp.fred_series} ({e})")
                if cached:
                    components[comp.fred_series] = SeriesFetch(
                        state.series_last_updated(comp.fred_series), cached, updated=False, stale=True
                    )

        releases, release_dates, published_dates = self._fetch_releases(
            ctx.http, config, api_key, today, warnings
        )
        meetings = self._fetch_meetings(ctx.http, today, warnings)
        statement, minutes = self._fetch_fomc(ctx, state, warnings)
        return RawData(
            today=today,
            retrieved_at=datetime.now(UTC),
            config=config,
            meetings=meetings,
            state=state,
            series=series,
            releases=releases,
            release_dates=release_dates,
            published_dates=published_dates,
            statement=statement,
            minutes=minutes,
            components=components,
            previous=ctx.previous_latest(),
            warnings=warnings,
        )

    @staticmethod
    def _fred_key_rejected(ctx: RunContext, series: list[str]) -> NoReturn:
        message = (
            f"FRED answered HTTP 400 to the first {len(series)} series ({', '.join(series)}) and "
            "accepted none: FRED_API_KEY is invalid, unregistered or empty. Update the "
            "FRED_API_KEY repository secret (Settings -> Secrets and variables -> Actions) "
            "with a key from https://fred.stlouisfed.org/docs/api/api_key.html."
        )
        log.error(message)
        ctx.alert(FRED_KEY_ALERT, message)
        raise RuntimeError(message)

    def _fetch_series(
        self, http: Http, sid: str, api_key: str, state: MacroState, config: MacroConfig, today: date
    ) -> SeriesFetch:
        meta = fetch_series_meta(http, sid, api_key)
        cached = load_cached_observations(sid)
        changed = has_series_changed(state.series_last_updated(sid), meta.last_updated)
        if not changed and cached is not None:
            return SeriesFetch(meta.last_updated, cached, updated=False)
        if cached:
            start = today - timedelta(days=LATER_RUN_LOOKBACK_DAYS)
        else:
            start = today.replace(year=today.year - config.settings.first_run_years)
        fresh = fetch_observations(http, sid, api_key, observation_start=start.isoformat())
        merged = merge_observations(cached, fresh)
        save_cached_observations(sid, merged)
        # A missing cache with unchanged last_updated (e.g. a fresh CI runner) refetches
        # history but is not new data: no events, no LLM.
        return SeriesFetch(meta.last_updated, merged, updated=changed)

    def _fetch_releases(
        self,
        http: Http,
        config: MacroConfig,
        api_key: str,
        today: date,
        warnings: list[str],
    ) -> tuple[dict[str, ReleaseRef], dict[str, list[date]], dict[str, list[date]]]:
        """Each series' release (HTTP-cached 30 days, §3), each release's full calendar,
        and — for non-daily releases, which are the only ones §5.5 can flag delayed —
        the dates FRED actually published data."""
        releases: dict[str, ReleaseRef] = {}
        for ind in config.indicators:
            sid = ind.fred_series
            if sid in releases:
                continue
            try:
                info = fetch_release(http, sid, api_key)
                releases[sid] = ReleaseRef(release_id=info.release_id, name=info.name)
            except (HttpError, KeyError, IndexError) as e:
                warnings.append(f"release lookup failed for {sid}: {e}")
        non_daily = {
            releases[i.fred_series].release_id
            for i in config.indicators
            if i.frequency != "daily" and i.fred_series in releases
        }
        release_dates: dict[str, list[date]] = {}
        published_dates: dict[str, list[date]] = {}
        start = (today - timedelta(days=RELEASE_DATES_LOOKBACK_DAYS)).isoformat()
        for release_id in sorted({r.release_id for r in releases.values()}):
            try:
                release_dates[release_id] = fetch_release_dates(
                    http, release_id, api_key, realtime_start=start
                )
                if release_id in non_daily:
                    published_dates[release_id] = fetch_release_dates(
                        http, release_id, api_key, realtime_start=start, published_only=True
                    )
            except (HttpError, KeyError) as e:
                warnings.append(f"release dates failed for release {release_id}: {e}")
        return releases, release_dates, published_dates

    def _fetch_meetings(self, http: Http, today: date, warnings: list[str]) -> list[FomcMeeting]:
        fallback = load_fomc_calendar(DEFAULT_FOMC_DATES_TOML).meetings
        try:
            live = fetch_calendar(http, fed_cache_dir() / "fomccalendars.htm")
        except HttpError as e:
            warnings.append(f"FOMC calendar page unavailable ({e}); using config/fomc_dates.toml")
            return fallback
        if not live:
            warnings.append("FOMC calendar page parsed no meetings; using config/fomc_dates.toml")
            return fallback
        for diff in compare_calendars(live, fallback, since=today):
            warnings.append(diff)
        return live

    def _fetch_fomc(
        self, ctx: RunContext, state: MacroState, warnings: list[str]
    ) -> tuple[StatementFetch | None, MinutesFetch | None]:
        http = ctx.http
        try:
            items = fetch_feed(http, fed_cache_dir() / "press_monetary.xml")
        except HttpError as e:
            warnings.append(f"Fed RSS feed unavailable ({e}); keeping the previous FOMC block")
            return None, None

        statement = None
        previous_output = ctx.previous_latest()
        prior_block = _previous_fomc_latest(previous_output, state)
        # With no previous FOMC block anywhere (a lost data branch and no legacy state),
        # rediscover the latest statement rather than publish none until the next meeting.
        since = (
            date.fromisoformat(state.fomc.latest_statement_date)
            if state.fomc.latest_statement_date and prior_block
            else None
        )
        stmts = statement_items(items)
        new = [i for i in stmts if since is None or parse_statement_date_from_url(i.link) > since]
        if new:
            latest_item = new[-1]
            idx = stmts.index(latest_item)
            latest = self._statement(ctx, latest_item.link, warnings)
            previous = self._statement(ctx, stmts[idx - 1].link, warnings) if idx > 0 else None
            if previous is None and prior_block:
                previous = _statement_from_block(prior_block)
            if latest is not None:
                statement = StatementFetch(latest=latest, previous=previous)

        minutes = None
        seen = (
            date.fromisoformat(state.fomc.latest_minutes_date)
            if state.fomc.latest_minutes_date and _previous_minutes(previous_output, state)
            else None
        )
        candidates = [m for m in minutes_items(items) if seen is None or m.meeting_date > seen]
        if candidates:
            item = candidates[-1]
            try:
                text = extract_minutes(fetch_page(http, item.url))
            except HttpError as e:
                warnings.append(f"minutes page {item.url} unavailable ({e})")
                text = ""
            if len(text) >= 1000:
                minutes = MinutesFetch(item=item, text=text)
            else:
                warnings.append(f"minutes extraction from {item.url} yielded {len(text)} chars; skipped")
        return statement, minutes

    def _statement(self, ctx: RunContext, url: str, warnings: list[str]) -> StatementInput | None:
        try:
            html = fetch_page(ctx.http, url)
        except HttpError as e:
            warnings.append(f"statement page {url} unavailable ({e})")
            return None
        extracted = extract_statement(html)
        if not is_extraction_valid(extracted):
            # §10: layout changed; keep the previous FOMC block rather than publish junk.
            message = f"statement extraction from {url} yielded {len(extracted.policy_text)} chars"
            warnings.append(message)
            log.error("statement extraction failed for %s; FOMC block not updated", url)
            # §10: a human has to fix the parser; one GitHub issue per week at most.
            ctx.alert(
                "macro: FOMC statement extraction failed",
                f"{message} (< 200). The previous FOMC block was kept. The page layout "
                "probably changed; see agents/macro/fomc.py:extract_statement.",
            )
            return None
        try:
            decision = parse_decision(extracted.policy_text)
        except ValueError:
            warnings.append(f"no target-range decision found in {url}")
            return None
        return StatementInput(
            date=parse_statement_date_from_url(url),
            url=url,
            extracted=extracted,
            decision=decision,
            votes=parse_votes(extracted.voting_text),
        )

    # ---- transform --------------------------------------------------------------------

    def transform(self, ctx: RunContext, raw: RawData) -> MacroData:
        cfg, state, today = raw.config, raw.state, raw.today
        indicator_names = {i.id: i.name for i in cfg.indicators}
        snapshots: dict[str, IndicatorSnapshot] = {}
        timings: dict[str, ReleaseTiming] = {}
        indicators: list[IndicatorOutput] = []
        events: list[Event] = []

        for ind in cfg.indicators:
            fetched = raw.series.get(ind.fred_series)
            if fetched is None:
                continue
            stored = state.series.get(ind.fred_series, SeriesState()).observations
            snapshot = compute_indicator_snapshot(ind, fetched.observations, stored, updated=fetched.updated)
            snapshots[ind.id] = snapshot
            events.extend(snapshot.events)
            release = raw.releases.get(ind.fred_series)
            timings[ind.id] = release_timing(
                frequency=ind.frequency,
                last_updated=date.fromisoformat(fetched.last_updated[:10]) if fetched.last_updated else None,
                release_dates=raw.release_dates.get(release.release_id, []) if release else [],
                published_dates=raw.published_dates.get(release.release_id) if release else None,
                today=today,
            )
            output = build_indicator(
                ind,
                fetched.observations,
                snapshot,
                timings[ind.id],
                publish_years=cfg.settings.publish_series_years,
                stale=fetched.stale,
            )
            if output is not None:
                indicators.append(output)

        # FOMC block: a new statement, or last run's block (re-cross-checked if pending).
        obs = {sid: f.observations for sid, f in raw.series.items()}
        upper, lower = obs.get("DFEDTARU", []), obs.get("DFEDTARL", [])
        fomc_latest: FomcLatest | None = None
        fomc_is_new = raw.statement is not None
        previous_block = _previous_fomc_latest(raw.previous, state)
        if raw.statement is not None:
            fomc_latest = build_fomc_latest(raw.statement.latest, raw.statement.previous)
        elif previous_block:
            fomc_latest = FomcLatest.model_validate(previous_block)
        if fomc_latest is not None and (fomc_is_new or fomc_latest.crosscheck_pending):
            fomc_latest, warning = crosscheck_target_range(fomc_latest, upper, lower)
            if warning:
                log.warning("%s", warning)
                raw.warnings.append(warning)
        fomc_evts = fomc_events(fomc_latest, is_new=fomc_is_new)
        for event in fomc_evts:
            event.facts["source_url"] = fomc_latest.url
        events.extend(fomc_evts)

        minutes_is_new = raw.minutes is not None
        minutes: FomcMinutesOut | None = None
        if raw.minutes is not None:
            item = raw.minutes.item
            minutes = FomcMinutesOut(
                meeting_date=item.meeting_date, released_at=item.released_at, url=item.url
            )
            for event in minutes_events(item.meeting_date, item.released_at, is_new=True):
                event.facts["source_url"] = item.url
                events.append(event)
        else:
            previous_minutes = _previous_minutes(raw.previous, state)
            if previous_minutes:
                minutes = FomcMinutesOut.model_validate(previous_minutes)

        target_range = (
            {"lower": fomc_latest.target_range.lower, "upper": fomc_latest.target_range.upper}
            if fomc_latest
            else target_range_from_fred(upper, lower)
        )
        regimes = build_regimes(
            RegimeInputs(
                core_pce=snapshots.get("core_pce"),
                unrate_series=obs.get("UNRATE", []),
                payrolls=snapshots.get("payrolls"),
                gdp=snapshots.get("gdp"),
                curve_10y2y=obs.get("T10Y2Y", []),
                policy_decision=fomc_latest.decision if fomc_latest else None,
                target_range=target_range,
            )
        )
        events.extend(regime_events(state.regimes, regimes, today=today))

        spreads = {
            sid: obs.get(sid, []) for sid in CURVE_SERIES if raw.series.get(sid) and raw.series[sid].updated
        }
        stored_latest = {
            sid: max((date.fromisoformat(d) for d in state.series[sid].observations), default=None)
            if sid in state.series
            else None
            for sid in spreads
        }
        events.extend(curve_sign_change_events(spreads, stored_latest))
        events.extend(delayed_events(timings, set(state.delayed), indicator_names))

        events = rank_events(events)
        if events:
            top = events[0]
            indicator_id = top.id.split(":")[1] if ":" in top.id else None
            headline = headline_for_event(top, indicator_name=indicator_names.get(indicator_id))
        elif _previous_headline(raw.previous, state):
            headline = _previous_headline(raw.previous, state)
        else:
            by_id = {i.id: i for i in indicators}
            headline = quiet_headline(
                [(label, _display_value(by_id[i])) for i, label in QUIET_HEADLINE_STATS if i in by_id]
            )

        calendar_input: dict[str, tuple[str, list[date], list[str]]] = {}
        for ind in cfg.indicators:
            release = raw.releases.get(ind.fred_series)
            if release is None or ind.frequency == "daily":
                continue  # daily H.15-style calendars would flood the list
            name, dates, ids = calendar_input.get(
                release.release_id, (release.name, raw.release_dates.get(release.release_id, []), [])
            )
            ids.append(ind.id)
            calendar_input[release.release_id] = (name, dates, ids)

        next_meeting = next(
            (
                m
                for m in sorted(raw.meetings, key=lambda m: m.start)
                if m.end >= today and not (fomc_latest and m.end <= fomc_latest.date)
            ),
            None,
        )
        yield_curve = build_yield_curve(obs, publish_years=cfg.settings.publish_series_years)
        if yield_curve is None:
            raise RuntimeError("yield curve series (DGS2, DGS10, T10Y2Y) unavailable")

        body = {
            "headline": headline,
            "key_stats": [k.model_dump(mode="json") for k in build_key_stats(indicators)],
            "regimes": regimes.model_dump(mode="json"),
            "indicators": [i.model_dump(mode="json") for i in indicators],
            "yield_curve": yield_curve.model_dump(mode="json"),
            "calendar": [c.model_dump(mode="json") for c in build_calendar(calendar_input, today=today)],
            "events": [
                EventOut(id=e.id, type=e.type, priority=e.priority, facts=e.facts).model_dump(mode="json")
                for e in events
            ],
            "next_meeting": FomcNextMeeting(
                start=next_meeting.start, end=next_meeting.end, has_sep=next_meeting.sep
            )
            if next_meeting
            else None,
        }

        new_state = MacroState(
            series={
                sid: SeriesState(
                    last_updated=f.last_updated,
                    observations=trim_observations({o.date.isoformat(): o.value for o in f.observations}),
                )
                for sid, f in {**raw.series, **raw.components}.items()
                if not f.stale
            },
            fomc=state.fomc,
            last_brief=state.last_brief,
            regimes={
                name: getattr(regimes, name).label
                for name in ("inflation", "labor", "growth", "policy", "curve")
            },
            delayed=sorted(i for i, t in timings.items() if t.delayed),
        )
        for sid, f in {**raw.series, **raw.components}.items():  # keep a failed series' state
            if f.stale and sid in state.series:
                new_state.series[sid] = state.series[sid]

        investigator = InvestigatorData(
            today=today,
            series={
                **{
                    c.id: SeriesSource(c, raw.components[c.fred_series].observations, kind="component")
                    for c in cfg.components
                    if c.fred_series in raw.components
                },
                **{
                    i.id: SeriesSource(i, raw.series[i.fred_series].observations)
                    for i in cfg.indicators
                    if i.fred_series in raw.series
                },
            },
            components={
                r: [c.id for c in cfg.components_for(r)] for r in {c.release for c in cfg.components}
            },
        )

        return MacroData(
            raw=raw,
            indicators=indicators,
            snapshots=snapshots,
            events=events,
            headline=headline,
            body=body,
            fomc_latest=fomc_latest,
            fomc_is_new=fomc_is_new,
            minutes=minutes,
            minutes_is_new=minutes_is_new,
            new_state=new_state,
            stale=any(f.stale for f in raw.series.values()),
            investigator=investigator,
            trigger=pick_trigger(events),
        )

    def summarize_dry_run(self, data: MacroData) -> str:
        lines = [f"{len(data.indicators)} indicators, {len(data.events)} events; headline: {data.headline}"]
        for ind in data.indicators:
            flags = " DELAYED" if ind.delayed else ""
            lines.append(
                f"  {ind.id:<22} {ind.primary.label:<16} {ind.primary.value!s:>10} {ind.primary.format:<24}"
                f" {ind.period_label:<14} next={ind.next_release}{flags}"
            )
        for name, regime in data.body["regimes"].items():
            lines.append(f"  regime {name:<10} {regime['label']:<20} {regime['detail']}")
        for e in data.events:
            lines.append(f"  event {e.priority:>3} {e.id}  {json.dumps(e.facts, default=str)[:160]}")
        if data.fomc_latest:
            f = data.fomc_latest
            lines.append(
                f"  fomc {f.date} {f.decision} {f.target_range.lower}-{f.target_range.upper} "
                f"change_bp={f.change_bp} changes={len(f.changes)} new={data.fomc_is_new}"
            )
        lines.append(f"  investigator trigger: {self.summarize_dry_run_trigger(data)}")
        for w in data.raw.warnings:
            lines.append(f"  warning: {w}")
        return "\n".join(lines)

    # ---- analyze ----------------------------------------------------------------------

    def analyze(self, ctx: RunContext, data: MacroData) -> AgentResult:
        raw, state = data.raw, data.new_state
        now = datetime.now(UTC)
        model = tier_config(TIER).model
        indicator_names = {i.id: i.name for i in raw.config.indicators}
        source_urls = {i.id: i.source_url for i in raw.config.indicators}
        warnings = list(raw.warnings)

        # No Anthropic key: every narrative is its deterministic template and the run
        # still publishes (status ok, with a warning) instead of failing.
        llm: LLM | None = ctx.llm
        needs_llm = bool(data.events) or data.fomc_is_new or data.minutes_is_new
        llm_problem = llm_key_problem(ctx.llm) if needs_llm else None
        if llm_problem:
            llm = None
            warnings.append(llm_problem)

        # What-changed brief (§7.2): only when something happened.
        if data.events:
            top = data.events[: raw.config.settings.max_events_to_llm]
            context = {
                "regimes": {k: v["label"] for k, v in data.body["regimes"].items()},
                "fed_target_range": {
                    "lower": data.fomc_latest.target_range.lower,
                    "upper": data.fomc_latest.target_range.upper,
                }
                if data.fomc_latest
                else None,
                "inflation_target_pct": 2,
            }
            result = generate_brief(
                llm,
                WhatChangedInput(as_of=raw.today.isoformat(), events=top, context=context),
                indicator_source_urls=source_urls,
                indicator_names=indicator_names,
            )
            brief = Brief(
                bullets=result.bullets,
                narrative_source=result.narrative_source,
                model=model if result.narrative_source == "llm" else None,
                generated_at=now,
                reused_from_run_id=None,
            )
            state.last_brief = LastBrief(
                run_id=ctx.run_id,
                bullets=[b.model_dump(mode="json") for b in brief.bullets],
                event_ids=[e.id for e in top],
                narrative_source=brief.narrative_source,
                model=brief.model,
                generated_at=now.isoformat(),
            )
        else:
            brief = _reused_brief(state.last_brief, now)

        # FOMC read (§7.3): only for a statement first seen this run.
        fomc_latest = data.fomc_latest
        if fomc_latest is not None and data.fomc_is_new:
            fomc_latest = fomc_latest.model_copy(update={"read": generate_fomc_read(llm, fomc_latest).read})
        if fomc_latest is not None:
            state.fomc.latest_statement_date = fomc_latest.date.isoformat()

        # Minutes summary (§5.7 step 6).
        minutes = data.minutes
        if minutes is not None and data.minutes_is_new:
            if raw.config.settings.summarize_minutes:
                summary, source = generate_minutes_summary(
                    llm,
                    text=raw.minutes.text,
                    meeting_date=minutes.meeting_date,
                    released_at=minutes.released_at,
                )
                minutes = minutes.model_copy(update={"summary": summary, "narrative_source": source})
            state.fomc.latest_minutes_date = minutes.meeting_date.isoformat()

        # Release investigator (§6.1): at most one agent loop, for a new high-priority
        # release or FOMC decision; otherwise the previous investigation is carried over.
        investigation = self._investigation(ctx, data, llm, fomc_latest, now, model, warnings)

        body = {k: v for k, v in data.body.items() if k != "next_meeting"}
        body["brief"] = brief.model_dump(mode="json")
        body["fomc"] = FomcBlock(
            latest=fomc_latest, next_meeting=data.body["next_meeting"], minutes=minutes
        ).model_dump(mode="json")
        body["investigation"] = investigation.model_dump(mode="json") if investigation else None
        for warning in raw.warnings:
            log.warning("%s", warning)

        # Persist only after every LLM call succeeded; a BudgetExceeded above leaves the
        # old state (and the old latest.json) in place.
        save_state(state, state_path())

        return AgentResult(
            body=body,
            sources=[
                Source(
                    name="FRED, Federal Reserve Bank of St. Louis",
                    url=FRED_SOURCE_URL,
                    retrieved_at=raw.retrieved_at,
                ),
                Source(name="Federal Reserve Board", url=FED_SOURCE_URL, retrieved_at=raw.retrieved_at),
            ],
            headline=data.headline,
            key_stats=build_key_stats(data.indicators),
            data_changed=bool(data.events),
            items_count=len(data.indicators),
            # §10: a failed series is `stale: true` on its indicator and a warning here.
            status="ok",
            warnings=warnings,
        )

    def _investigation(
        self,
        ctx: RunContext,
        data: MacroData,
        llm: LLM | None,
        fomc_latest: FomcLatest | None,
        now: datetime,
        model: str,
        warnings: list[str],
    ) -> Investigation | None:
        previous = (data.raw.previous or {}).get("investigation")
        if data.trigger is None or data.investigator is None:
            if not previous:
                return None
            carried = Investigation.model_validate(previous)
            if carried.reused_from_run_id is None:
                run_id = (data.raw.previous or {}).get("meta", {}).get("run_id")
                carried = carried.model_copy(update={"reused_from_run_id": run_id})
            return carried

        regimes = data.body["regimes"]
        data.investigator.fomc = fomc_context(
            fomc_latest=fomc_latest,
            next_meeting=data.body["next_meeting"],
            policy_regime=regimes["policy"]["label"] if "policy" in regimes else None,
        )
        with tracing.span("custom", "macro:investigate", trigger=data.trigger.event_id) as sp:
            result = investigate(llm, data.investigator, data.trigger)
            sp.set(narrative_source=result.narrative_source, cited=len(result.cited_series))
        if result.warning:
            warnings.append(result.warning)
        return _investigation_block(result, data.investigator, now, model)

    def summarize_dry_run_trigger(self, data: MacroData) -> str:
        return data.trigger.event_id if data.trigger else "none"


def _investigation_block(
    result: InvestigationResult, investigator: InvestigatorData, now: datetime, model: str
) -> Investigation:
    loop = result.loop
    return Investigation(
        trigger=InvestigationTrigger(
            event_id=result.trigger.event_id,
            type=result.trigger.type,
            indicator_id=result.trigger.indicator_id,
        ),
        analysis=result.draft.analysis,
        cited_series=[
            CitedSeries(
                id=i,
                name=investigator.series[i].config.name,
                fred_series=investigator.series[i].config.fred_series,
                url=investigator.series[i].url,
            )
            for i in result.cited_series
        ],
        narrative_source=result.narrative_source,
        model=model if result.narrative_source == "llm" else None,
        generated_at=now,
        loop=InvestigationLoop(
            steps=loop.steps,
            tool_calls=loop.tools_called(),
            stop_reason=loop.stop_reason,
            cost_usd=round(loop.usd, 6),
            guard_attempts=loop.guard_attempts,
        )
        if loop is not None
        else None,
    )


# ---- the previous run's output (restored from the data branch) --------------------------
#
# A no-change run republishes the previous FOMC block, minutes and headline. They come
# from the previous latest.json; state.json only held them before agents-core v0.2.0
# restored the data branch in CI, so they're read from there once, as a migration.


def _previous_fomc_latest(previous: dict | None, state: MacroState) -> dict | None:
    if previous is not None:
        return (previous.get("fomc") or {}).get("latest")
    return state.fomc.latest


def _previous_minutes(previous: dict | None, state: MacroState) -> dict | None:
    if previous is not None:
        return (previous.get("fomc") or {}).get("minutes")
    return state.fomc.minutes


def _previous_headline(previous: dict | None, state: MacroState) -> str | None:
    if previous is not None:
        return previous.get("headline")
    return state.last_brief.headline if state.last_brief else None


def _statement_from_block(block: dict) -> StatementInput | None:
    """Rebuild the previous statement from state when it has scrolled off the feed."""
    try:
        latest = FomcLatest.model_validate(block)
    except ValueError:
        return None
    return StatementInput(
        date=latest.date,
        url=latest.url,
        extracted=ExtractedStatement(policy_text=latest.latest_text, voting_text=""),
        decision=Decision(
            decision=latest.decision,
            target_range={"lower": latest.target_range.lower, "upper": latest.target_range.upper},
            change_bp=latest.change_bp,
        ),
        votes=Votes(for_count=latest.votes.for_count, against=[a.model_dump() for a in latest.votes.against]),
    )


QUIET_HEADLINE_STATS = (("cpi", "CPI YoY"), ("unrate", "unemployment"), ("treasury_10y", "10Y"))


def _display_value(ind: IndicatorOutput) -> str:
    suffix = "%" if ind.primary.format.startswith("percent") else ""
    return f"{ind.primary.value}{suffix}"


def _reused_brief(last: LastBrief | None, now: datetime) -> Brief:
    """§6: with no events, the brief is copied from the previous run."""
    if last is None:
        return Brief(
            bullets=[], narrative_source="template", model=None, generated_at=now, reused_from_run_id=None
        )
    return Brief(
        bullets=[BriefBullet.model_validate(b) for b in last.bullets],
        narrative_source=last.narrative_source,
        model=last.model,
        generated_at=datetime.fromisoformat(last.generated_at) if last.generated_at else now,
        reused_from_run_id=last.run_id,
    )


AGENT = MacroAgent()
