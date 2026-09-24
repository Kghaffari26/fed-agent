"""§5.7 extraction, decision parsing, and vote parsing, against the 3
reconstructed statement fixtures (see tests/fixtures/fomc/README.md)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.macro.fomc import (
    extract_statement,
    is_extraction_valid,
    parse_decision,
    parse_votes,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fomc"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def jan_statement():
    return extract_statement(_load("statement_2026_01_28.html"))


@pytest.fixture
def mar_statement():
    return extract_statement(_load("statement_2026_03_18.html"))


@pytest.fixture
def sep_statement():
    return extract_statement(_load("statement_2026_09_16.html"))


# -- extraction ---------------------------------------------------------------


def test_extraction_drops_release_time_header(jan_statement):
    assert "For release at" not in jan_statement.policy_text


def test_extraction_drops_implementation_note_link(jan_statement):
    assert "Implementation Note" not in jan_statement.policy_text


def test_extraction_drops_media_inquiries_footer(jan_statement):
    assert "media inquiries" not in jan_statement.policy_text


def test_extraction_separates_voting_paragraph(jan_statement):
    assert "Voting for" not in jan_statement.policy_text
    assert jan_statement.voting_text.startswith("Voting for")


def test_extraction_keeps_policy_paragraphs(jan_statement):
    assert "maximum employment" in jan_statement.policy_text
    assert "target range for the federal funds rate" in jan_statement.policy_text


def test_extraction_missing_article_returns_empty():
    statement = extract_statement("<html><body><p>no article div here</p></body></html>")
    assert statement.policy_text == ""
    assert statement.voting_text == ""


def test_is_extraction_valid_true_for_real_statement(jan_statement):
    assert is_extraction_valid(jan_statement) is True


def test_is_extraction_valid_false_when_too_short():
    statement = extract_statement("<html><body><div id='article'><p>short</p></div></body></html>")
    assert is_extraction_valid(statement) is False


# -- decision parsing: hold, cut, fractional ranges ----------------------------


def test_decision_hold_january(jan_statement):
    decision = parse_decision(jan_statement.policy_text)
    assert decision.decision == "hold"
    assert decision.target_range == {"lower": 4.25, "upper": 4.5}


def test_decision_cut_september(sep_statement):
    decision = parse_decision(sep_statement.policy_text)
    assert decision.decision == "cut"
    assert decision.target_range == {"lower": 4.0, "upper": 4.25}


def test_decision_change_bp_computed_against_previous_range():
    previous_range = {"lower": 4.25, "upper": 4.5}
    decision = parse_decision(
        "the Committee decided to lower the target range for the federal funds rate to 4 to 4-1/4 percent.",
        previous_range=previous_range,
    )
    assert decision.change_bp == -25


def test_decision_change_bp_none_without_previous_range(sep_statement):
    decision = parse_decision(sep_statement.policy_text)
    assert decision.change_bp is None


def test_decision_hold_zero_change_bp():
    previous_range = {"lower": 4.25, "upper": 4.5}
    decision = parse_decision(
        "the Committee decided to maintain the target range for the federal funds rate "
        "at 4-1/4 to 4-1/2 percent.",
        previous_range=previous_range,
    )
    assert decision.change_bp == 0


def test_decision_hike_verb_and_whole_number_range():
    decision = parse_decision(
        "the Committee decided to raise the target range for the federal funds rate to 5 to 5-1/4 percent."
    )
    assert decision.decision == "hike"
    assert decision.target_range == {"lower": 5.0, "upper": 5.25}


def test_decision_no_match_raises():
    with pytest.raises(ValueError):
        parse_decision("The Committee discussed the economic outlook at length.")


# -- vote parsing: unanimous and with a dissent --------------------------------


def test_votes_unanimous_no_dissent(jan_statement):
    votes = parse_votes(jan_statement.voting_text)
    assert votes.for_count == 11  # Powell + Williams (named) + nine other members
    assert votes.against == []


def test_votes_with_one_dissent(sep_statement):
    votes = parse_votes(sep_statement.voting_text)
    assert votes.for_count == 10  # Powell + Williams (named) + eight other members
    assert len(votes.against) == 1
    assert votes.against[0]["name"] == "Michael J. Reeves"
    assert "maintain the target range" in votes.against[0]["preferred"]


def test_votes_empty_text_returns_zero_and_no_dissents():
    votes = parse_votes("")
    assert votes.for_count == 0
    assert votes.against == []


def test_votes_multiple_dissents_parsed_as_separate_entries():
    text = (
        "Voting for the monetary policy action were Jerome H. Powell, Chair; and ten other members. "
        "Voting against this action were Alice Smith and Bob Jones, who preferred a 25 basis point cut."
    )
    votes = parse_votes(text)
    names = {d["name"] for d in votes.against}
    assert names == {"Alice Smith", "Bob Jones"}
    assert all(d["preferred"] == "a 25 basis point cut" for d in votes.against)
