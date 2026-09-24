"""Deterministic number guard shared by every agent.

See docs/specs/SPEC_MACRO.md §7.4. `verify_numbers` extracts every numeric
token from a piece of LLM-written text and checks that each one is backed by
a value in `facts`, so a model can never publish a number it didn't compute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

SCALES = {"K": 1e3, "M": 1e6, "B": 1e9}

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"
)

# Spans matching these patterns are never treated as numeric tokens worth
# verifying, per §7.4 step 2. Each pattern's capture group is the ignored span.
_IGNORE_PATTERNS = [
    re.compile(rf"\b(?:{_MONTHS})\.?\s+(\d{{1,2}})\b"),  # day-of-month after a month name
    re.compile(r"\b(\d{1,2})-(?:year|month)s?\b", re.IGNORECASE),  # 2-year, 10-year, 3-month
    re.compile(r"\bQ([1-4])\b"),  # Q1-Q4
    re.compile(r"\b(401)\(k\)", re.IGNORECASE),  # 401(k)
    re.compile(r"\b(\d+)(?:st|nd|rd|th)\b"),  # ordinals
]

# A standalone 4-digit year (1900-2100), not part of a larger number.
_YEAR_RE = re.compile(r"(?<![\d,.$])\b(19\d{2}|20\d{2}|2100)\b(?!\.\d)(?![%KMB])")

# One token regex covering $1,234.5 / 2.9% / -0.3 / +142K / 1.2M / 4-1/4 / 25 bp / 0.25 pp.
_TOKEN_RE = re.compile(
    r"""
    (?P<sign>[+-])?
    (?P<dollar>\$)?
    (?P<intpart>\d{1,3}(?:,\d{3})+|\d+)
    (?:-(?P<fracnum>\d+)/(?P<fracden>\d+))?
    (?:\.(?P<dec>\d+))?
    (?P<scale>[KMB])?
    (?P<percent>%)?
    (?:[ ](?P<unit>bp|pp)\b)?
    """,
    re.VERBOSE,
)


@dataclass
class GuardResult:
    ok: bool
    unsupported: list[str] = field(default_factory=list)


@dataclass
class _NumberToken:
    text: str
    start: int
    end: int
    value: float
    decimals: int
    scale: float


def _ignored_spans(text: str, allow: Iterable[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in _IGNORE_PATTERNS:
        for m in pattern.finditer(text):
            spans.append(m.span(1))
    for m in _YEAR_RE.finditer(text):
        spans.append(m.span(1))
    for term in allow:
        if not term:
            continue
        start = 0
        while (idx := text.find(term, start)) != -1:
            spans.append((idx, idx + len(term)))
            start = idx + len(term)
    return spans


def _span_ignored(span: tuple[int, int], ignored: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start >= i_start and end <= i_end for i_start, i_end in ignored)


def _extract_tokens(text: str, allow: Iterable[str]) -> list[_NumberToken]:
    ignored = _ignored_spans(text, allow)
    tokens: list[_NumberToken] = []
    for m in _TOKEN_RE.finditer(text):
        if not m.group("intpart"):
            continue
        if _span_ignored(m.span("intpart"), ignored):
            continue

        sign = -1.0 if m.group("sign") == "-" else 1.0
        int_part = float(m.group("intpart").replace(",", ""))
        decimals = 0
        value = int_part

        if m.group("fracnum") and m.group("fracden"):
            value += float(m.group("fracnum")) / float(m.group("fracden"))
            decimals = 2
        if m.group("dec"):
            value = float(f"{int(int_part)}.{m.group('dec')}")
            decimals = len(m.group("dec"))

        scale = SCALES.get(m.group("scale") or "", 1.0)
        tokens.append(
            _NumberToken(
                text=m.group(0).strip(),
                start=m.start(),
                end=m.end(),
                value=sign * value,
                decimals=decimals,
                scale=scale,
            )
        )
    return tokens


def verify_numbers(text: str, facts: Iterable[float], *, allow: Iterable[str] = ()) -> GuardResult:
    """Check that every numeric token in `text` is backed by a value in `facts`.

    Implements docs/specs/SPEC_MACRO.md §7.4: extract numeric tokens (ignoring
    years, day-of-month numbers, ordinals, known terms like "10-year", and
    anything in `allow`), then require each token to match some fact once
    scaled and rounded to the token's own decimal precision.
    """
    fact_list = [f for f in facts if f is not None]
    unsupported: list[str] = []
    for token in _extract_tokens(text, allow):
        target = round(abs(token.value), token.decimals)
        matched = any(
            round(abs(fact) / token.scale, token.decimals) == target for fact in fact_list
        )
        if not matched:
            unsupported.append(token.text)
    return GuardResult(ok=not unsupported, unsupported=unsupported)
