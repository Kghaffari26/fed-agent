"""The release investigator: a budgeted tool-use loop (`agents_core.agent_loop`) that
explains what's driving a new high-priority release. §6.1 of SPEC_MACRO.md.

It runs at most once per run, only when the run has a new CPI, core PCE, payrolls,
unemployment or GDP release, or an FOMC decision (`pick_trigger`). The model decides
what to look at through six tools, all served from data `transform` already computed
(no network, no LLM inside a tool):

    get_series(id, range)                 displayed values of one series
    get_components(release)               CPI components / payrolls by sector
    percentile_vs_history(id, value, years)
    compare_to_prior_cycles(id)           the 2019 and 2022-23 windows
    get_fomc_context()                    target range, last decision, next meeting
    finish(analysis, cited_series)        the loop's built-in result tool

Numbers still come from code: every number a tool returns is rounded by `display.py`
(what the site shows), and the number guard checks the finished `analysis` against
the trigger's facts plus every tool output of this loop — a number the model didn't
read from a tool fails the guard, is retried once, then the deterministic
`template_investigation` ships (`narrative_source: "template"`). Citations are
attached by code, only for series a tool actually returned. Budget: 8 model steps,
$0.08 (§12), 120 s; a budget stop is a graceful template fallback, never a failed run.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from agents_core.agent_loop import AgentLoop, LoopBudget, LoopResult, ToolError, tool
from agents_core.costs import BudgetExceeded
from agents_core.guards import fields_guard
from agents_core.llm import LLM
from pydantic import BaseModel, Field

from agents.macro.config import IndicatorConfig
from agents.macro.display import TRANSFORM_LABELS, display_for, units_display
from agents.macro.events import Event
from agents.macro.fetch_fred import Observation
from agents.macro.transform import resample_month_end, transform_series

log = logging.getLogger(__name__)

PROMPT_VERSION = "investigator-2026-09-27"
PURPOSE = "macro:investigator"
TIER = "smart"
MAX_STEPS = 8
MAX_USD = 0.08
MAX_SECONDS = 120.0
MAX_TOKENS = 1000  # per response; the worst case each step is checked at uses this
MAX_WORDS = 120

# The releases that trigger an investigation (the brief's high-priority set).
TRIGGER_INDICATORS = ("cpi", "core_pce", "payrolls", "unrate", "gdp")
COMPONENT_RELEASES = ("cpi", "payrolls")
RANGES: dict[str, int] = {"1y": 1, "2y": 2, "5y": 5, "10y": 10}
# compare_to_prior_cycles windows (inclusive).
CYCLES: dict[str, tuple[date, date]] = {
    "2019": (date(2019, 1, 1), date(2019, 12, 31)),
    "2022-23": (date(2022, 1, 1), date(2023, 12, 31)),
}
# Names, not facts: tenors and the cycle window labels.
GUARD_ALLOW = ("2022-23", "2022–23", "10Y–2Y", "10Y-2Y", "10Y–3M", "10Y-3M", "3M", "2Y", "5Y", "10Y", "30Y")

SYSTEM_PROMPT = """\
You are the release investigator for a public US macro dashboard. A new data release (or an FOMC \
decision) just came out; explain in plain English what is driving it.

How to work:
- Use the tools to look at the release's components, its history, where it sits versus the past \
(percentile_vs_history) and versus the 2019 and 2022-23 cycles, and the FOMC context, as useful. \
Stay focused: a few well-chosen calls, then finish. You have at most 8 turns.
- Then call `finish` with `analysis` (2-4 sentences, at most 120 words) and `cited_series` (the ids \
of every series whose numbers you used).

Rules for `analysis`:
1. Use ONLY numbers that appear in the task or in tool results, written exactly as given (same \
decimals). Never compute, estimate, or recall a number yourself: no differences, no sums, no averages.
2. Say what drove the change (which components or sectors, and how it compares with history). \
Neutral tone. No predictions, no policy advice, no adjectives like "shocking" or "massive".
3. Rates and percentage changes are in percent; changes in rates are in percentage points (pp); \
payroll changes are in thousands of jobs (e.g. +22K).
"""


class InvestigationDraft(BaseModel):
    """The `finish` tool's input."""

    analysis: str = Field(description="2-4 sentences, at most 120 words, using only numbers from tools")
    cited_series: list[str] = Field(description="Ids of every series whose numbers the analysis uses")


# ---- tool inputs --------------------------------------------------------------------


class SeriesArgs(BaseModel):
    id: str = Field(description="Series id, e.g. 'cpi', 'core_pce', 'unrate', 'cpi_shelter'")
    range: Literal["1y", "2y", "5y", "10y"] = Field(
        default="2y", description="How far back; 5y and 10y are sampled quarterly"
    )


class ComponentsArgs(BaseModel):
    release: Literal["cpi", "payrolls"] = Field(description="The release to break down")


class PercentileArgs(BaseModel):
    id: str = Field(description="Series id")
    value: float = Field(description="A value of this series, as shown by a tool")
    years: int = Field(default=10, ge=1, le=10, description="History window in years")


class CycleArgs(BaseModel):
    id: str = Field(description="Series id")


class NoArgs(BaseModel):
    pass


# ---- data ------------------------------------------------------------------------------


@dataclass
class SeriesSource:
    config: IndicatorConfig
    observations: list[Observation]
    kind: Literal["indicator", "component"] = "indicator"

    @property
    def url(self) -> str:
        return self.config.source_url


@dataclass
class InvestigatorData:
    """Everything the tools can read, built by `transform` (pure)."""

    today: date
    series: dict[str, SeriesSource]  # by indicator/component id
    components: dict[str, list[str]]  # release -> component ids
    fomc: dict[str, Any] = field(default_factory=dict)

    def get(self, series_id: str) -> SeriesSource:
        if series_id not in self.series:
            raise ToolError(f"Unknown series id {series_id!r}. Valid ids: {', '.join(sorted(self.series))}")
        return self.series[series_id]


@dataclass
class Trigger:
    event_id: str
    type: str
    indicator_id: str | None
    facts: dict[str, Any]


def pick_trigger(events: list[Event]) -> Trigger | None:
    """The highest-ranked new high-priority release or FOMC decision (events are
    already ranked). None: no investigation this run."""
    for e in events:
        if e.type == "fomc_decision":
            return Trigger(event_id=e.id, type=e.type, indicator_id=None, facts=e.facts)
        if e.type == "new_release":
            indicator_id = e.id.split(":")[1]
            if indicator_id in TRIGGER_INDICATORS:
                return Trigger(event_id=e.id, type=e.type, indicator_id=indicator_id, facts=e.facts)
    return None


# ---- computed views (pure; what the tools return) -------------------------------------


def _primary_series(src: SeriesSource, since: date | None) -> list[Observation]:
    cfg = src.config
    obs = src.observations
    if cfg.frequency in ("daily", "weekly"):
        obs = resample_month_end(obs)
        freq = "monthly"
    else:
        freq = cfg.frequency
    return [
        o
        for o in transform_series(cfg.primary, obs, freq, primary=cfg.primary, since=since)
        if o.value is not None
    ]


def _rounded(src: SeriesSource, value: float | None) -> float | int | None:
    return display_for(src.config, src.config.primary).round(value)


def _describe(series_id: str, src: SeriesSource) -> dict[str, Any]:
    cfg = src.config
    return {
        "id": series_id,
        "name": cfg.name,
        "measure": TRANSFORM_LABELS[cfg.primary],
        "units": units_display(cfg),
    }


def series_view(data: InvestigatorData, series_id: str, range_: str = "2y") -> dict[str, Any]:
    src = data.get(series_id)
    years = RANGES[range_]
    since = data.today.replace(year=data.today.year - years)
    points = _primary_series(src, since)
    if years > 2 and src.config.frequency != "quarterly":
        points = points[::-3][::-1]  # quarterly sampling, always keeping the latest
    values = [[o.date.isoformat(), _rounded(src, o.value)] for o in points]
    return {
        **_describe(series_id, src),
        "range": range_,
        "latest": values[-1] if values else None,
        "observations": values,
    }


def components_view(data: InvestigatorData, release: str) -> dict[str, Any]:
    ids = data.components.get(release, [])
    if not ids:
        raise ToolError(f"No components configured for {release!r}. Available: {sorted(data.components)}")
    out = []
    for cid in ids:
        src = data.series.get(cid)
        if src is None:
            continue
        points = _primary_series(src, data.today.replace(year=data.today.year - 2))
        if not points:
            continue
        row: dict[str, Any] = {
            **_describe(cid, src),
            "period": points[-1].date.isoformat(),
            "latest": _rounded(src, points[-1].value),
            "prior": _rounded(src, points[-2].value) if len(points) > 1 else None,
        }
        for name in src.config.secondary:
            secondary = [
                o
                for o in transform_series(
                    name, src.observations, src.config.frequency, primary=src.config.primary
                )[-1:]
                if o.value is not None
            ]
            if secondary:
                row[TRANSFORM_LABELS[name]] = display_for(src.config, name).round(secondary[-1].value)
        out.append(row)
    if not out:
        raise ToolError(f"No component data available for {release!r} this run.")
    return {"release": release, "components": out}


def percentile_view(data: InvestigatorData, series_id: str, value: float, years: int = 10) -> dict[str, Any]:
    src = data.get(series_id)
    since = data.today.replace(year=data.today.year - years)
    history = [_rounded(src, o.value) for o in _primary_series(src, since)]
    history = [v for v in history if v is not None]
    if len(history) < 12:
        raise ToolError(f"Not enough history for {series_id!r} over {years} years.")
    below = sum(v < value for v in history)
    equal = sum(v == value for v in history)
    percentile = round(100 * (below + 0.5 * equal) / len(history))
    return {
        **_describe(series_id, src),
        "value": value,
        "years": years,
        "observations": len(history),
        "percentile": percentile,
        "history_min": min(history),
        "history_max": max(history),
    }


def cycles_view(data: InvestigatorData, series_id: str) -> dict[str, Any]:
    src = data.get(series_id)
    points = _primary_series(src, None)
    if not points:
        raise ToolError(f"No data for {series_id!r}.")
    windows: dict[str, Any] = {}
    for label, (start, end) in CYCLES.items():
        window = [_rounded(src, o.value) for o in points if start <= o.date <= end]
        window = [v for v in window if v is not None]
        if not window:
            windows[label] = None
            continue
        disp = display_for(src.config, src.config.primary)
        windows[label] = {
            "start_year": start.year,
            "end_year": end.year,
            "average": disp.round(statistics.fmean(window) * disp.divisor),
            "low": min(window),
            "high": max(window),
        }
    return {
        **_describe(series_id, src),
        "current": {"period": points[-1].date.isoformat(), "value": _rounded(src, points[-1].value)},
        "windows": windows,
    }


# ---- the loop --------------------------------------------------------------------------


def build_tools(data: InvestigatorData, record: Callable[[str, Any], None]) -> list:
    """The investigator's tools over `data`. `record(tool, output)` sees every
    successful output (it feeds the guard's facts and the citation check)."""

    def recorded(name: str, output: Any) -> Any:
        record(name, output)
        return output

    @tool(timeout_seconds=10)
    def get_series(args: SeriesArgs) -> dict:
        """Recent history of one series as the dashboard shows it (its primary measure,
        e.g. CPI year-over-year %, payrolls monthly change in thousands, the unemployment
        rate in %). Indicator ids: cpi, core_cpi, pce, core_pce, unrate, payrolls, gdp,
        claims, treasury_10y... and component ids from get_components."""
        return recorded("get_series", series_view(data, args.id, args.range))

    @tool(timeout_seconds=10)
    def get_components(args: ComponentsArgs) -> dict:
        """The latest reading of each component of a release: for CPI, shelter, energy,
        food, core goods and core services (YoY % and MoM %); for payrolls, the monthly
        change by major sector (thousands)."""
        return recorded("get_components", components_view(data, args.release))

    @tool(timeout_seconds=10)
    def percentile_vs_history(args: PercentileArgs) -> dict:
        """Where a value of a series ranks against that series' own history over the
        past `years` years (0 = lowest, 100 = highest)."""
        return recorded("percentile_vs_history", percentile_view(data, args.id, args.value, args.years))

    @tool(timeout_seconds=10)
    def compare_to_prior_cycles(args: CycleArgs) -> dict:
        """The series' current value next to its average, low and high in 2019 (the
        last pre-pandemic year) and in 2022-23 (the inflation surge and hiking cycle)."""
        return recorded("compare_to_prior_cycles", cycles_view(data, args.id))

    @tool(timeout_seconds=10)
    def get_fomc_context(args: NoArgs) -> dict:
        """The Fed's current target range, its latest decision and vote, the tone of the
        latest statement versus the previous one, and the next meeting."""
        if not data.fomc:
            raise ToolError("No FOMC context available this run.")
        return recorded("get_fomc_context", data.fomc)

    return [get_series, get_components, percentile_vs_history, compare_to_prior_cycles, get_fomc_context]


def task_message(trigger: Trigger, as_of: date) -> str:
    return json.dumps(
        {
            "as_of": as_of.isoformat(),
            "trigger": {
                "event_id": trigger.event_id,
                "type": trigger.type,
                "series_id": trigger.indicator_id,
                "facts": trigger.facts,
            },
            "instruction": "Explain what is driving this release, then call finish.",
        },
        sort_keys=True,
    )


@dataclass
class Investigation:
    """What `investigate` returns: the result plus what the loop did (for §6.1 and evals)."""

    trigger: Trigger
    draft: InvestigationDraft
    narrative_source: Literal["llm", "template"]
    cited_series: list[str]
    loop: LoopResult[InvestigationDraft] | None
    warning: str | None = None


class _Recorder:
    def __init__(self, trigger: Trigger) -> None:
        self.facts: list[Any] = [trigger.facts]
        self.series_seen: set[str] = {trigger.indicator_id} if trigger.indicator_id else set()

    def __call__(self, name: str, output: Any) -> None:
        self.facts.append(output)
        if name == "get_components":
            self.series_seen.update(c["id"] for c in output["components"])
        elif "id" in output:
            self.series_seen.add(output["id"])


def build_loop(
    llm: LLM, data: InvestigatorData, trigger: Trigger, recorder: _Recorder | None = None
) -> tuple[AgentLoop[InvestigationDraft], _Recorder]:
    """Shared by the agent and the trajectory evals (evals/macro/suites.py)."""
    recorder = recorder or _Recorder(trigger)

    def guard(value: InvestigationDraft):
        # Built at finish time, so the facts include every tool output so far.
        return fields_guard(recorder.facts, ["analysis"], allow=GUARD_ALLOW)(value)

    loop = AgentLoop(
        llm,
        tools=build_tools(data, recorder),
        result_model=InvestigationDraft,
        system=SYSTEM_PROMPT,
        tier=TIER,
        max_tokens=MAX_TOKENS,
        budget=LoopBudget(max_steps=MAX_STEPS, max_usd=MAX_USD, max_seconds=MAX_SECONDS),
        guard=guard,
        fallback=lambda: template_investigation(data, trigger),
        finish_description="Submit the analysis and the ids of the series it cites. Call exactly once.",
        purpose=PURPOSE,
    )
    return loop, recorder


def investigate(llm: LLM | None, data: InvestigatorData, trigger: Trigger) -> Investigation:
    """Run the loop (or, with `llm=None` — no API key — go straight to the template).
    Never raises for a loop failure: any non-finished stop ships the template with a
    warning."""
    if llm is None:
        draft = template_investigation(data, trigger)
        return Investigation(trigger, draft, "template", _valid_cited(draft.cited_series, data, None), None)
    loop, recorder = build_loop(llm, data, trigger)
    try:
        result = loop.run(task_message(trigger, data.today))
    except BudgetExceeded:
        raise  # the loop turns budget stops into results; anything left is the run's cap
    except Exception as e:  # an API error: the investigation is additive, never fatal
        log.warning("release investigator failed: %s", e)
        draft = template_investigation(data, trigger)
        return Investigation(
            trigger,
            draft,
            "template",
            _valid_cited(draft.cited_series, data, None),
            None,
            warning=f"release investigator failed ({type(e).__name__}); published the template analysis",
        )
    if result.ok and result.result is not None:
        draft = result.result
        source = result.narrative_source or "llm"
        cited = _valid_cited(draft.cited_series, data, recorder.series_seen if source == "llm" else None)
        if not cited:
            cited = _valid_cited(template_investigation(data, trigger).cited_series, data, None)
        return Investigation(trigger, _trim(draft), source, cited, result)
    draft = template_investigation(data, trigger)
    return Investigation(
        trigger,
        draft,
        "template",
        _valid_cited(draft.cited_series, data, None),
        result,
        warning=f"release investigator stopped ({result.stop_reason}); published the template analysis",
    )


def _valid_cited(ids: list[str], data: InvestigatorData, seen: set[str] | None) -> list[str]:
    """Known series only and, for model output, only series a tool actually returned."""
    out = []
    for i in ids:
        if i in data.series and (seen is None or i in seen) and i not in out:
            out.append(i)
    return out


def _trim(draft: InvestigationDraft) -> InvestigationDraft:
    words = draft.analysis.split()
    if len(words) <= MAX_WORDS:
        return draft
    text = " ".join(words[:MAX_WORDS])
    cut = text.rfind(". ")
    return draft.model_copy(update={"analysis": text[: cut + 1] if cut > 0 else text})


# ---- deterministic fallback -----------------------------------------------------------


def _fmt(value: float | int | None, units: str) -> str:
    if value is None:
        return "n/a"
    if units == "thousands":
        return f"{value:+,}K"
    if units in ("percent", "pp"):
        return f"{value}%"
    return f"{value}"


def template_investigation(data: InvestigatorData, trigger: Trigger) -> InvestigationDraft:
    """Built from the same data the tools serve; never calls the model, never raises."""
    if trigger.type == "fomc_decision":
        ctx = data.fomc
        rng = ctx.get("target_range") or {}
        text = (
            f"The FOMC's decision was a {ctx.get('decision', 'policy decision')}, leaving the target range "
            f"at {rng.get('lower')}–{rng.get('upper')}%."
        )
        if ctx.get("policy_regime"):
            text += f" The policy regime reads {ctx['policy_regime']}."
        cited = [i for i in ("core_pce", "unrate") if i in data.series]
        return InvestigationDraft(analysis=text, cited_series=cited)

    indicator_id = trigger.indicator_id or ""
    src = data.series.get(indicator_id)
    if src is None:
        return InvestigationDraft(analysis="No series data was available for this release.", cited_series=[])
    units = units_display(src.config)
    points = _primary_series(src, None)
    latest = _rounded(src, points[-1].value) if points else None
    text = f"{src.config.name} ({TRANSFORM_LABELS[src.config.primary]}) was {_fmt(latest, units)}"
    if len(points) > 1:
        text += f", after {_fmt(_rounded(src, points[-2].value), units)} the prior period"
    text += "."
    cited = [indicator_id]
    if indicator_id in data.components:
        try:
            comps = components_view(data, indicator_id)["components"]
        except ToolError:
            comps = []
        if comps:
            parts = [f"{c['name'].split(': ', 1)[-1]} {_fmt(c['latest'], c['units'])}" for c in comps]
            text += f" By component: {', '.join(parts)}."
            cited += [c["id"] for c in comps]
    return InvestigationDraft(analysis=text, cited_series=cited)


def fomc_context(
    *,
    fomc_latest: Any | None,
    next_meeting: Any | None,
    policy_regime: str | None,
) -> dict[str, Any]:
    """`get_fomc_context`'s payload from the §6 FOMC block (models or None)."""
    if fomc_latest is None:
        return {}
    ctx: dict[str, Any] = {
        "date": fomc_latest.date.isoformat(),
        "decision": fomc_latest.decision,
        "target_range": {"lower": fomc_latest.target_range.lower, "upper": fomc_latest.target_range.upper},
        "change_bp": fomc_latest.change_bp,
        "votes_for": fomc_latest.votes.for_count,
        "dissents": len(fomc_latest.votes.against),
        "tone_shift": fomc_latest.read.tone_shift if fomc_latest.read else None,
        "policy_regime": policy_regime,
    }
    if next_meeting is not None:
        ctx["next_meeting"] = {"start": next_meeting.start.isoformat(), "end": next_meeting.end.isoformat()}
    return ctx
