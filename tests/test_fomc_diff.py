"""§5.7 step 5: sentence diff — added/removed/modified classification and
abbreviation-safe sentence splitting, against real statement pages."""

from __future__ import annotations

from pathlib import Path

from agents.macro.fomc import diff_statements, extract_statement, split_sentences

FIXTURES = Path(__file__).parent / "fixtures" / "fomc"


def _policy_text(name: str) -> str:
    return extract_statement((FIXTURES / name).read_text()).policy_text


# -- abbreviation-safe sentence splitting ------------------------------------------


def test_split_sentences_handles_us_abbreviation():
    text = "The U.S. economy grew. Inflation eased."
    sentences = split_sentences(text)
    assert sentences == ["The U.S. economy grew.", "Inflation eased."]


def test_split_sentences_handles_percent_and_initials():
    text = "Jerome H. Powell spoke. Core CPI rose 2.9 percent."
    sentences = split_sentences(text)
    assert len(sentences) == 2
    assert sentences[0].startswith("Jerome H. Powell")


# -- real statement pairs ------------------------------------------------------------


def test_diff_near_identical_real_pair_yields_one_modified_change():
    # June -> July 2026: only "reaffirmed its policy" -> "is continuing its policy" changed.
    changes = diff_statements(
        _policy_text("statement_2026_06_17.html"), _policy_text("statement_2026_07_29.html")
    )
    assert len(changes) == 1
    assert changes[0].type == "modified"
    assert "reaffirmed its policy" in changes[0].before
    assert "is continuing its policy" in changes[0].after


def test_diff_identical_statements_yields_no_changes():
    text = _policy_text("statement_2026_07_29.html")
    assert diff_statements(text, text) == []


def test_diff_real_hike_pair():
    changes = diff_statements(
        _policy_text("statement_2026_07_29.html"), _policy_text("statement_2026_09_16.html")
    )
    by_type = {t: [c for c in changes if c.type == t] for t in ("added", "removed", "modified")}
    assert all(by_type.values())
    decision = changes[0]
    assert decision.type == "modified"
    assert "maintain the target range" in decision.before
    assert "raise the target range" in decision.after
    added = [c.after for c in by_type["added"]]
    assert "Inflation remains elevated." in added
    assert [c.idx for c in changes] == sorted(c.idx for c in changes)


def test_diff_real_cut_pair_2024():
    changes = diff_statements(
        _policy_text("statement_2024_07_31.html"), _policy_text("statement_2024_09_18.html")
    )
    afters = " ".join(c.after or "" for c in changes)
    assert "Job gains have slowed" in afters
    assert "lower the target range" in afters


def test_diff_modified_pair_has_high_similarity_ratio():
    # "Job gains have remained solid..." -> "Job gains have slowed..." should
    # pair as modified (ratio >= 0.6), not show up as separate added/removed.
    before_text = "Job gains have remained solid in recent months, and the unemployment rate has stayed low."
    after_text = (
        "Job gains have slowed in recent months, and the unemployment rate has edged up but remains low."
    )
    changes = diff_statements(before_text, after_text)
    assert len(changes) == 1
    assert changes[0].type == "modified"


def test_diff_completely_different_sentences_are_removed_and_added_not_modified():
    before_text = "The Committee discussed the economic outlook."
    after_text = "Chair Powell announced a new framework for communications."
    changes = diff_statements(before_text, after_text)
    types = sorted(c.type for c in changes)
    assert types == ["added", "removed"]


def test_diff_added_sentence_has_before_none():
    before_text = "Inflation has eased."
    after_text = "Inflation has eased. A new paragraph was added here."
    changes = diff_statements(before_text, after_text)
    assert len(changes) == 1
    assert changes[0].type == "added"
    assert changes[0].before is None
    assert changes[0].after == "A new paragraph was added here."


def test_diff_removed_sentence_has_after_none():
    before_text = "Inflation has eased. This sentence will be removed."
    after_text = "Inflation has eased."
    changes = diff_statements(before_text, after_text)
    assert len(changes) == 1
    assert changes[0].type == "removed"
    assert changes[0].after is None
