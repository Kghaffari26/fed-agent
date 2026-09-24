"""Deterministic fallback text and headline templates.

Used two places: the §6 `headline` is *always* rendered this way from the
top-ranked event (no LLM involved), and a bullet falls back to a template
sentence when the number guard fails twice (§7.4's retry policy, via
`core.llm.call_with_number_guard`'s `template_fallback`), which flips that
bullet's `narrative_source` to `"template"`.
"""

from __future__ import annotations

from agents.macro.events import Event


def _fmt_pct(value: float, *, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.1f}%"


def _fmt_pp(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f} pp"


def _fmt_thousands(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.0f}K"


def headline_for_event(event: Event, *, indicator_name: str | None = None) -> str:
    """Render the top-priority event as the §6 `headline`. Purely deterministic."""
    facts = event.facts

    if event.type == "fomc_decision":
        decision = facts.get("decision", "held rates")
        target_range = facts.get("target_range") or {}
        verb = {"hold": "held rates steady", "cut": "cut rates", "hike": "raised rates"}.get(
            decision, "acted on rates"
        )
        range_str = ""
        if target_range:
            range_str = f" to {target_range.get('lower')}–{target_range.get('upper')}%"
        return f"The FOMC {verb}{range_str}."

    if event.type == "new_release":
        name = indicator_name or "The indicator"
        if "yoy" in facts:
            prior = facts.get("prior_yoy")
            prior_str = f" (prior {_fmt_pct(prior)})" if prior is not None else ""
            return f"{name} came in at {_fmt_pct(facts['yoy'])} YoY{prior_str}."
        if "mom_diff" in facts:
            return f"{name} changed by {_fmt_thousands(facts['mom_diff'])}."
        if "level" in facts:
            return f"{name} stands at {facts['level']}."
        return f"{name} was updated."

    if event.type == "revision":
        old, new = facts.get("old"), facts.get("new")
        period = facts.get("period", "the prior period")
        return f"{indicator_name or 'A prior release'} for {period} was revised from {old} to {new}."

    if event.type == "threshold_cross":
        direction = "above" if facts.get("direction") == "up" else "below"
        return f"{indicator_name or 'The indicator'} crossed {direction} {facts.get('threshold')}."

    if event.type == "curve_sign_change":
        return "The yield curve changed sign."

    if event.type == "regime_change":
        regime = facts.get("regime", "economic")
        return f"The {regime} regime shifted from {facts.get('old')} to {facts.get('new')}."

    if event.type == "minutes_released":
        return "The FOMC released minutes from its latest meeting."

    if event.type == "delayed":
        return f"{indicator_name or 'A scheduled release'} is delayed."

    if event.type == "extreme":
        kind = facts.get("kind", "extreme")
        return f"{indicator_name or 'The indicator'} hit a new 12-month {kind}."

    return "The macro dashboard was updated."


def template_bullet_for_event(event: Event, *, indicator_name: str | None = None) -> str:
    """A single-sentence, guard-safe fallback bullet for one event."""
    return headline_for_event(event, indicator_name=indicator_name)


def template_brief(events: list[Event], indicator_names: dict[str, str] | None = None) -> list[str]:
    """The full deterministic fallback brief: one templated sentence per
    event, in priority order, used when the LLM's brief fails the guard
    twice (§7.4/§10)."""
    names = indicator_names or {}
    bullets = []
    for event in events:
        indicator_id = event.id.split(":")[1] if ":" in event.id else None
        name = names.get(indicator_id) if indicator_id else None
        bullets.append(template_bullet_for_event(event, indicator_name=name))
    return bullets
