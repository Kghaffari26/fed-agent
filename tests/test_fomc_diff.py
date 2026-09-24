"""§5.7 step 5: sentence diff — added/removed/modified classification and
abbreviation-safe sentence splitting, against the reconstructed fixtures."""

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


# -- near-identical pair: mostly "equal", one "modified" ---------------------------


def test_diff_near_identical_statements_yields_one_modified_change():
    jan = _policy_text("statement_2026_01_28.html")
    mar = _policy_text("statement_2026_03_18.html")
    changes = diff_statements(jan, mar)
    assert len(changes) == 1
    assert changes[0].type == "modified"
    assert "roughly in balance" in changes[0].before
    assert "uncertainty around the economic outlook has increased" in changes[0].after


def test_diff_identical_statements_yields_no_changes():
    jan = _policy_text("statement_2026_01_28.html")
    assert diff_statements(jan, jan) == []


# -- substantially reworded pair: modified + added/removed ------------------------


def test_diff_reworded_statements_yields_multiple_changes():
    mar = _policy_text("statement_2026_03_18.html")
    sep = _policy_text("statement_2026_09_16.html")
    changes = diff_statements(mar, sep)
    assert len(changes) >= 2
    types = {c.type for c in changes}
    assert "modified" in types


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
