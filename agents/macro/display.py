"""Publish-time rounding and §6 display formats (§5.1: "Round only at publish time").

Transforms return full precision; everything that reaches latest.json — indicator
values, sparklines, event facts, regime details — is rounded here, from the
indicator's configured `units`/`level_decimals`/`level_divisor` and the transform.
Event facts are rounded with the same rule, so the numbers the LLM sees (and the
number guard checks against) are exactly the numbers the site shows.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_core.schema import StatFormat

from agents.macro.config import IndicatorConfig, Transform

# Short keys used in event facts and the §6 secondary labels.
TRANSFORM_KEYS: dict[str, str] = {
    "yoy_pct": "yoy",
    "mom_pct": "mom",
    "mom_diff": "mom_diff",
    "ann_3m_pct": "ann_3m",
    "avg_3": "avg_3",
    "avg_4w": "avg_4w",
    "change_pp": "change_pp",
    "level": "level",
}

TRANSFORM_LABELS: dict[str, str] = {
    "yoy_pct": "YoY",
    "mom_pct": "MoM",
    "mom_diff": "MoM change",
    "ann_3m_pct": "3-mo annualized",
    "avg_3": "3-mo avg",
    "avg_4w": "4-wk avg",
    "change_pp": "Change",
    "level": "Level",
}

# Every format published here is one of agents-core's standard `StatFormat`s (the
# schema enforces it, and tests/test_display.py checks every configured indicator).
_LEVEL_FORMATS: dict[str, StatFormat] = {
    "percent": "percent",
    "pp": "pp_signed",
    "count": "count",
    "thousands": "count",
    "millions": "decimal1",
    "index": "decimal1",
    "dollars": "currency",
}
_DELTA_FORMATS: dict[str, StatFormat] = {
    "percent": "pp_signed",
    "percent_signed": "pp_signed",
    "pp_signed": "pp_signed",
    "count": "count_signed",
    "count_signed": "count_signed",
    "count_signed_thousands": "count_signed_thousands",
    "decimal1": "decimal1",
    "currency": "currency",
}


@dataclass(frozen=True)
class Display:
    format: StatFormat
    decimals: int
    divisor: float = 1.0

    def round(self, value: float | None) -> float | int | None:
        if value is None:
            return None
        rounded = round(value / self.divisor, self.decimals)
        if self.decimals == 0:
            return int(rounded)  # counts: 162, not 162.0 (what the LLM sees is what it writes)
        return rounded + 0.0  # normalize -0.0


def _counts_in_thousands(ind: IndicatorConfig) -> bool:
    return ind.primary == "mom_diff" and ind.units == "thousands"


def display_for(ind: IndicatorConfig, transform: Transform) -> Display:
    level = Display(_LEVEL_FORMATS[ind.units], ind.level_decimals, ind.level_divisor)
    if transform in ("yoy_pct", "ann_3m_pct"):
        return Display("percent", 1)
    if transform == "mom_pct":
        return Display("percent_signed", 1)
    if transform == "mom_diff" or (transform == "avg_3" and ind.primary == "mom_diff"):
        fmt = "count_signed_thousands" if _counts_in_thousands(ind) else "count_signed"
        return Display(fmt, 0, ind.level_divisor)
    if transform == "change_pp":
        return Display(_DELTA_FORMATS[level.format], level.decimals, level.divisor)
    return level  # level, avg_3, avg_4w


def delta_display(ind: IndicatorConfig) -> Display:
    """The §6 `change` block: primary vs the prior period's primary."""
    primary = display_for(ind, ind.primary)
    return Display(_DELTA_FORMATS[primary.format], primary.decimals, primary.divisor)


def units_display(ind: IndicatorConfig) -> str:
    fmt = display_for(ind, ind.primary).format
    if fmt.startswith("percent"):
        return "percent"
    if fmt == "pp_signed":
        return "pp"
    if fmt == "count_signed_thousands":
        return "thousands"
    return ind.units


def rounded(ind: IndicatorConfig, transform: Transform, value: float | None) -> float | None:
    return display_for(ind, transform).round(value)


def displayed_delta(ind: IndicatorConfig, value: float | None, prior: float | None) -> float | int | None:
    """Change between two primary values *as displayed*: 3.36 vs 3.31 shows as 3.4 vs
    3.3, so the delta is +0.1, never a contradictory 0.0 from rounding the raw 0.05."""
    primary = display_for(ind, ind.primary)
    a, b = primary.round(value), primary.round(prior)
    if a is None or b is None:
        return None
    return delta_display(ind).round((a - b) * primary.divisor)
