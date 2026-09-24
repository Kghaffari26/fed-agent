"""FOMC statement processing: extraction, decision/vote parsing, sentence diff.

See docs/specs/SPEC_MACRO.md §3 and §5.7. Operates entirely on already-fetched
HTML/text, so it's fully testable without network access.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

import pysbd
from selectolax.parser import HTMLParser

MIN_VALID_EXTRACTION_CHARS = 200
SENTENCE_MODIFIED_RATIO_THRESHOLD = 0.6

_FRACTIONS = {"1/4": 0.25, "1/2": 0.5, "3/4": 0.75}

_DECISION_RE = re.compile(
    r"(maintain|lower|raise)\s+the target range for the federal funds rate\s+(?:at|to)\s+"
    r"([\d/\-]+)\s+to\s+([\d/\-]+)\s+percent",
    re.IGNORECASE,
)
_DECISION_MAP = {"maintain": "hold", "lower": "cut", "raise": "hike"}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

_FOR_RE = re.compile(r"Voting for the monetary policy action (?:was|were) (.+?)(?<!\b[A-Z])\.(?:\s|$)")
_AGAINST_RE = re.compile(r"Voting against this action (?:was|were) (.+?), who preferred (.+?)\.")
_OTHER_MEMBERS_RE = re.compile(r"^and\s+(\w+)\s+other members?$", re.IGNORECASE)


@dataclass
class ExtractedStatement:
    policy_text: str
    voting_text: str


@dataclass
class Decision:
    decision: str  # "hold" | "cut" | "hike"
    target_range: dict[str, float]
    change_bp: int | None


@dataclass
class Votes:
    for_count: int
    against: list[dict[str, str]]


@dataclass
class Change:
    idx: int
    type: str  # "added" | "removed" | "modified"
    before: str | None
    after: str | None


# --------------------------------------------------------------------------
# Extraction (§3)
# --------------------------------------------------------------------------


def extract_statement(html: str) -> ExtractedStatement:
    tree = HTMLParser(html)
    article = tree.css_first("div#article")
    if article is None:
        return ExtractedStatement(policy_text="", voting_text="")

    policy_parts: list[str] = []
    voting_text = ""
    for node in article.css("p"):
        text = node.text(strip=True)
        if not text:
            continue
        if text.startswith("Implementation Note"):
            continue
        if text.startswith("Voting for"):
            voting_text = text
            continue
        policy_parts.append(text)

    return ExtractedStatement(policy_text=" ".join(policy_parts), voting_text=voting_text)


def is_extraction_valid(statement: ExtractedStatement) -> bool:
    """§10: extraction yielding < 200 chars means the page layout changed."""
    return len(statement.policy_text) >= MIN_VALID_EXTRACTION_CHARS


# --------------------------------------------------------------------------
# Decision parsing (§5.7 step 3)
# --------------------------------------------------------------------------


def _parse_rate_number(token: str) -> float:
    match = re.match(r"^(\d+)(?:-(\d+/\d+))?$", token.strip())
    if not match:
        raise ValueError(f"unparseable rate number: {token!r}")
    whole = float(match.group(1))
    frac = _FRACTIONS.get(match.group(2), 0.0) if match.group(2) else 0.0
    return whole + frac


def parse_decision(policy_text: str, previous_range: dict[str, float] | None = None) -> Decision:
    match = _DECISION_RE.search(policy_text)
    if not match:
        raise ValueError("could not find a target-range decision sentence in policy_text")
    verb, lower_token, upper_token = match.groups()
    lower = _parse_rate_number(lower_token)
    upper = _parse_rate_number(upper_token)
    change_bp = round((lower - previous_range["lower"]) * 100) if previous_range else None
    return Decision(
        decision=_DECISION_MAP[verb.lower()],
        target_range={"lower": lower, "upper": upper},
        change_bp=change_bp,
    )


# --------------------------------------------------------------------------
# Vote parsing (§5.7 step 4)
# --------------------------------------------------------------------------


def _split_names(segment: str) -> list[str]:
    parts = re.split(r",\s*and\s+|\s+and\s+|,\s*", segment.strip())
    return [p.strip() for p in parts if p.strip()]


def _count_for_votes(segment: str) -> int:
    count = 0
    for chunk in segment.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        other_match = _OTHER_MEMBERS_RE.match(chunk)
        if other_match:
            count += _NUMBER_WORDS.get(other_match.group(1).lower(), 0)
        else:
            # one named "Name, Title" (or bare "and Name") entry
            chunk = re.sub(r"^and\s+", "", chunk, flags=re.IGNORECASE)
            if chunk:
                count += 1
    return count


def parse_votes(voting_text: str) -> Votes:
    for_count = 0
    for_match = _FOR_RE.search(voting_text)
    if for_match:
        for_count = _count_for_votes(for_match.group(1))

    against: list[dict[str, str]] = []
    against_match = _AGAINST_RE.search(voting_text)
    if against_match:
        names_segment, preferred = against_match.groups()
        for name in _split_names(names_segment):
            against.append({"name": name, "preferred": preferred.strip()})

    return Votes(for_count=for_count, against=against)


# --------------------------------------------------------------------------
# Sentence diff (§5.7 step 5)
# --------------------------------------------------------------------------

_segmenter = pysbd.Segmenter(language="en", clean=False)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _segmenter.segment(text) if s.strip()]


def _pair_replace_block(
    before_block: list[str], after_block: list[str]
) -> list[tuple[str, str | None, str | None]]:
    used_after: set[int] = set()
    results: list[tuple[str, str | None, str | None]] = []
    for before in before_block:
        best_j, best_ratio = None, 0.0
        for j, after in enumerate(after_block):
            if j in used_after:
                continue
            ratio = difflib.SequenceMatcher(a=before, b=after).ratio()
            if ratio > best_ratio:
                best_ratio, best_j = ratio, j
        if best_j is not None and best_ratio >= SENTENCE_MODIFIED_RATIO_THRESHOLD:
            used_after.add(best_j)
            results.append(("modified", before, after_block[best_j]))
        else:
            results.append(("removed", before, None))
    for j, after in enumerate(after_block):
        if j not in used_after:
            results.append(("added", None, after))
    return results


def diff_statements(previous_text: str, latest_text: str) -> list[Change]:
    prev_sentences = split_sentences(previous_text)
    new_sentences = split_sentences(latest_text)
    matcher = difflib.SequenceMatcher(a=prev_sentences, b=new_sentences, autojunk=False)

    changes: list[Change] = []
    idx = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            idx += i2 - i1
            continue
        if tag == "replace":
            for kind, before, after in _pair_replace_block(prev_sentences[i1:i2], new_sentences[j1:j2]):
                changes.append(Change(idx=idx, type=kind, before=before, after=after))
                idx += 1
        elif tag == "delete":
            for sentence in prev_sentences[i1:i2]:
                changes.append(Change(idx=idx, type="removed", before=sentence, after=None))
                idx += 1
        elif tag == "insert":
            for sentence in new_sentences[j1:j2]:
                changes.append(Change(idx=idx, type="added", before=None, after=sentence))
                idx += 1
    return changes
