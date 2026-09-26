"""Deterministic text: the §6 headline, and every narrative fallback.

Used in three places:
- the §6 `headline` is *always* rendered here from the top-ranked event (no LLM);
- `template_brief` is the `fallback=` for the what-changed brief when the number
  guard fails twice (§7.4), which sets `narrative_source: "template"`;
- `template_fomc_read` / `template_minutes_summary` are the fallbacks for the FOMC
  read and the minutes summary.

Every number written here comes straight from an event's `facts` (already rounded
to published precision by `display`), so template text always passes the guard.
"""

from __future__ import annotations

from datetime import date

from agents.macro.events import Event


def _signed(value: float, decimals: int) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{decimals}f}"


def _decimals(value: float) -> int:
    text = repr(float(value))
    return 0 if text.endswith(".0") else len(text.split(".")[1])


def _fmt_pct(value: float, *, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.1f}%"


def _fmt_pp(value: float) -> str:
    return f"{_signed(value, 2)} pp"


def _fmt_thousands(value: float) -> str:
    return f"{_signed(value, 0)}K"


def format_level(value: float, units: str | None) -> str:
    """A level at its published precision, with its unit."""
    d = _decimals(value)
    if units == "percent":
        return f"{value:.{d}f}%"
    if units == "pp":
        return f"{_signed(value, d)} pp"
    if units == "thousands":
        return f"{value:,.{d}f}K"
    if units == "millions":
        return f"{value:.{d}f} million"
    if units == "count":
        return f"{value:,.{d}f}"
    if units == "dollars":
        return f"${value:.{d}f}"
    return f"{value:.{d}f}"


def _format_change(value: float, units: str | None) -> str:
    d = _decimals(value)
    if units in ("percent", "pp"):
        return f"{_signed(value, d)} pp"
    if units == "thousands":
        return f"{_signed(value, d)}K"
    return _signed(value, d)


def _format_fact(key: str, value: float, units: str | None) -> str:
    if key in ("yoy", "ann_3m"):
        return _fmt_pct(value)
    if key == "mom":
        return _fmt_pct(value, signed=True)
    if key in ("mom_diff", "avg_3") and units == "thousands":
        return _fmt_thousands(value)
    if key in ("change_pp",):
        return _format_change(value, units)
    return format_level(value, units)


def _period_label(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return d.strftime("%b %Y")


def headline_for_event(event: Event, *, indicator_name: str | None = None) -> str:
    """Render the top-priority event as the §6 `headline`. Purely deterministic."""
    facts = event.facts
    name = indicator_name or facts.get("indicator")
    units = facts.get("units")

    if event.type == "fomc_decision":
        decision = facts.get("decision", "hold")
        target_range = facts.get("target_range") or {}
        verb = {"hold": "held rates steady", "cut": "cut rates", "hike": "raised rates"}.get(
            decision, "acted on rates"
        )
        range_str = ""
        if target_range:
            range_str = (
                " at" if decision == "hold" else " to"
            ) + f" {target_range['lower']:.2f}–{target_range['upper']:.2f}%"
        return f"The FOMC {verb}{range_str}."

    if event.type == "new_release":
        name = name or "The indicator"
        if "yoy" in facts and facts["yoy"] is not None:
            prior = facts.get("prior_yoy")
            prior_str = f" (prior {_fmt_pct(prior)})" if prior is not None else ""
            return f"{name} came in at {_fmt_pct(facts['yoy'])} YoY{prior_str}."
        if "mom" in facts and facts["mom"] is not None and facts.get("prior_mom") is not None:
            return (
                f"{name} changed {_fmt_pct(facts['mom'], signed=True)} MoM "
                f"(prior {_fmt_pct(facts['prior_mom'], signed=True)})."
            )
        if "mom_diff" in facts and facts["mom_diff"] is not None:
            return f"{name} changed by {_fmt_thousands(facts['mom_diff'])}."
        if "level" in facts and facts["level"] is not None:
            prior = facts.get("prior_level")
            prior_str = f" (prior {format_level(prior, units)})" if prior is not None else ""
            return f"{name} stands at {format_level(facts['level'], units)}{prior_str}."
        return f"{name} was updated."

    if event.type == "revision":
        old, new = facts.get("old"), facts.get("new")
        label = _period_label(facts.get("period")) or "the prior period"
        if units == "thousands":
            old_s, new_s = _fmt_thousands(old), _fmt_thousands(new)
        else:
            old_s, new_s = str(old), str(new)
        return f"{name or 'A prior release'} for {label} was revised from {old_s} to {new_s}."

    if event.type == "threshold_cross":
        direction = "above" if facts.get("direction") == "up" else "below"
        return f"{name or 'The indicator'} crossed {direction} {facts.get('threshold')}."

    if event.type == "curve_sign_change":
        curve = facts.get("curve", "The yield curve")
        state = "turned positive" if facts.get("new_sign") == "positive" else "inverted"
        return f"{curve} {state}."

    if event.type == "regime_change":
        regime = facts.get("regime", "economic")
        return f"The {regime} regime shifted from {facts.get('old')} to {facts.get('new')}."

    if event.type == "minutes_released":
        return "The FOMC released minutes from its latest meeting."

    if event.type == "delayed":
        return f"{name or 'A scheduled release'} is delayed."

    if event.type == "extreme":
        kind = facts.get("kind", "extreme")
        return f"{name or 'The indicator'} hit a new 12-month {kind}."

    return "The macro dashboard was updated."


def template_bullet_for_event(event: Event, *, indicator_name: str | None = None) -> str:
    """A single-sentence, guard-safe fallback bullet for one event."""
    return headline_for_event(event, indicator_name=indicator_name)


def template_brief(events: list[Event], indicator_names: dict[str, str] | None = None) -> list[str]:
    """The full deterministic fallback brief: one templated sentence per event, in
    priority order (§7.4/§10)."""
    names = indicator_names or {}
    bullets = []
    for event in events:
        indicator_id = event.id.split(":")[1] if ":" in event.id else None
        name = names.get(indicator_id) if indicator_id else None
        bullets.append(template_bullet_for_event(event, indicator_name=name))
    return bullets


def quiet_headline(key_values: list[tuple[str, str]]) -> str:
    """Headline for a run with no events and no previous headline to reuse."""
    parts = "; ".join(f"{label} {value}" for label, value in key_values)
    return f"No new releases. {parts}." if parts else "No new releases."


_TONE_FROM_DECISION = {"hike": "more_hawkish", "cut": "more_dovish", "hold": "unchanged"}


def template_fomc_read(
    *, decision: str, target_range: dict, change_bp: int | None, changes: list[dict]
) -> dict:
    """FOMC read fallback. The tone comes from the rate decision alone and says so."""
    verb = {
        "hold": "held the target range at",
        "cut": "lowered the target range to",
        "hike": "raised the target range to",
    }
    summary = (
        f"The FOMC {verb.get(decision, 'set the target range at')} "
        f"{target_range['lower']:.2f}–{target_range['upper']:.2f}%."
    )
    if change_bp:
        summary += f" That is a change of {change_bp:+d} bp."
    if changes:
        summary += f" {len(changes)} sentences changed from the previous statement."
    else:
        summary += " The statement text is unchanged from the previous one."
    decision_idx = [
        c["idx"] for c in changes if "target range" in ((c.get("after") or "") + (c.get("before") or ""))
    ]
    cited = decision_idx[:1] or [c["idx"] for c in changes[:1]]
    tone = _TONE_FROM_DECISION.get(decision, "unchanged") if changes else "unchanged"
    return {
        "summary": summary,
        "tone_shift": tone,
        "rationale": "Automatic fallback: tone inferred from the rate decision only, not from a reading "
        "of the statement's language.",
        "cited_change_idx": cited if tone != "unchanged" or changes else [],
        "key_phrases": [],
    }


def template_minutes_summary(*, meeting_date: date, released_at: date) -> str:
    return (
        f"Minutes of the FOMC meeting that ended {meeting_date:%B} {meeting_date.day}, {meeting_date.year} "
        f"were released {released_at:%B} {released_at.day}, {released_at.year}. "
        "An automatic summary was not available for this release; see the full text."
    )
