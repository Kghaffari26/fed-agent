"""The "Style" eval (§11): no banned words, no advice language, bullets <=30 words.

Pure and independent of agents_core — runs against any generated bullet text,
whether from a real LLM call or the template fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_BULLET_WORDS = 30

BANNED_WORDS = [
    "shocking", "massive", "surge", "plunge", "crash", "skyrocket", "collapse",
    "unprecedented", "alarming", "catastrophic", "explosive",
]

ADVICE_PATTERNS = [
    r"\byou should\b", r"\bwe recommend\b", r"\bbuy\b", r"\bsell\b",
    r"\binvest(?:ment)? advice\b", r"\bnow is the time\b", r"\bconsider (?:buying|selling)\b",
]


@dataclass
class StyleViolation:
    bullet_index: int
    kind: str  # "banned_word" | "advice_language" | "too_long"
    detail: str


@dataclass
class StyleResult:
    ok: bool
    violations: list[StyleViolation] = field(default_factory=list)


def _word_count(text: str) -> int:
    return len(text.split())


def check_style(bullets: list[str]) -> StyleResult:
    violations: list[StyleViolation] = []
    for i, bullet in enumerate(bullets):
        lower = bullet.lower()
        if _word_count(bullet) > MAX_BULLET_WORDS:
            violations.append(
                StyleViolation(bullet_index=i, kind="too_long", detail=f"{_word_count(bullet)} words")
            )
        for word in BANNED_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", lower):
                violations.append(StyleViolation(bullet_index=i, kind="banned_word", detail=word))
        for pattern in ADVICE_PATTERNS:
            if re.search(pattern, lower):
                violations.append(StyleViolation(bullet_index=i, kind="advice_language", detail=pattern))
    return StyleResult(ok=not violations, violations=violations)
