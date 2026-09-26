"""§5.7 extraction, decision parsing, and vote parsing against real statement pages
saved from federalreserve.gov (see tests/fixtures/fomc/README.md). Three different
years and three page generations: 2022 and 2024 (named "Voting for" paragraph) and
2026 (a "by a 9 – 3 vote" preface that names only dissenters)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.macro.fomc import (
    extract_minutes,
    extract_statement,
    is_extraction_valid,
    normalize_text,
    parse_decision,
    parse_votes,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fomc"
ALL_STATEMENTS = sorted(p.name for p in FIXTURES.glob("statement_*.html"))


def _statement(name: str):
    return extract_statement((FIXTURES / name).read_text())


def test_at_least_three_years_of_real_fixtures():
    years = {name.split("_")[1] for name in ALL_STATEMENTS}
    assert len(years) >= 3


# -- extraction ---------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_STATEMENTS)
def test_extraction_drops_page_chrome(name):
    text = _statement(name).policy_text
    assert "For release at" not in text
    assert "Implementation Note" not in text
    assert "media inquiries" not in text
    assert "Share" not in text
    assert not text[:20].startswith(("January", "June", "July", "September"))  # article__time date


@pytest.mark.parametrize("name", ALL_STATEMENTS)
def test_extraction_is_valid_and_keeps_the_decision_sentence(name):
    statement = _statement(name)
    assert is_extraction_valid(statement)
    assert "target range for the federal funds rate" in statement.policy_text


@pytest.mark.parametrize("name", ALL_STATEMENTS)
def test_extraction_separates_voting_text(name):
    statement = _statement(name)
    assert "Voting for" not in statement.policy_text
    assert "Voting against" not in statement.policy_text
    assert "approved the following statement" not in statement.policy_text
    assert statement.voting_text


def test_extraction_does_not_glue_words_around_inline_tags():
    # The 2026-09-16 page has "percentage point<strong> </strong>to 3-3/4<strong> </strong>to 4".
    text = _statement("statement_2026_09_16.html").policy_text
    assert "by 1/4 percentage point to 3-3/4 to 4 percent" in text
    assert "geopolitical developments, domestic spending" in text


def test_extraction_folds_non_breaking_hyphens():
    # 2022-06-15 writes "1‑1/2" with a non-breaking hyphen.
    text = _statement("statement_2022_06_15.html").policy_text
    assert "1-1/2 to 1-3/4 percent" in text
    assert "‑" not in text


def test_extraction_drops_link_only_paragraphs():
    assert "Plans for Reducing" not in _statement("statement_2022_06_15.html").voting_text


def test_extraction_missing_article_returns_empty():
    statement = extract_statement("<html><body><p>no article div here</p></body></html>")
    assert statement.policy_text == ""
    assert statement.voting_text == ""


def test_is_extraction_valid_false_when_too_short():
    statement = extract_statement("<html><body><div id='article'><p>short</p></div></body></html>")
    assert is_extraction_valid(statement) is False


def test_normalize_text():
    assert normalize_text("a  b\n c‑d") == "a b c-d"


# -- decision parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "decision", "lower", "upper", "change_bp"),
    [
        ("statement_2022_06_15.html", "hike", 1.5, 1.75, None),  # no "by" wording, no previous range
        ("statement_2024_07_31.html", "hold", 5.25, 5.5, 0),
        ("statement_2024_09_18.html", "cut", 4.75, 5.0, -50),  # "by 1/2 percentage point"
        ("statement_2026_06_17.html", "hold", 3.5, 3.75, 0),
        ("statement_2026_07_29.html", "hold", 3.5, 3.75, 0),
        ("statement_2026_09_16.html", "hike", 3.75, 4.0, 25),  # "by 1/4 percentage point"
    ],
)
def test_decision_parse_on_real_statements(name, decision, lower, upper, change_bp):
    parsed = parse_decision(_statement(name).policy_text)
    assert parsed.decision == decision
    assert parsed.target_range == {"lower": lower, "upper": upper}
    assert parsed.change_bp == change_bp


def test_decision_change_bp_prefers_previous_range():
    text = _statement("statement_2022_06_15.html").policy_text
    parsed = parse_decision(text, previous_range={"lower": 0.75, "upper": 1.0})
    assert parsed.change_bp == 75


def test_decision_bare_fraction_lower_bound():
    parsed = parse_decision(
        "the Committee decided to raise the target range for the federal funds rate to 3/4 to 1 percent"
    )
    assert parsed.target_range == {"lower": 0.75, "upper": 1.0}


def test_decision_no_match_raises():
    with pytest.raises(ValueError):
        parse_decision("The Committee discussed the economic outlook at length.")


# -- vote parsing -----------------------------------------------------------------


def test_votes_named_list_unanimous():
    votes = parse_votes(_statement("statement_2024_07_31.html").voting_text)
    assert votes.for_count == 12
    assert votes.against == []


def test_votes_named_list_with_one_dissent():
    votes = parse_votes(_statement("statement_2024_09_18.html").voting_text)
    assert votes.for_count == 11
    assert votes.against == [
        {
            "name": "Michelle W. Bowman",
            "preferred": "to lower the target range for the federal funds rate by 1/4 percentage point "
            "at this meeting",
        }
    ]


def test_votes_2022_dissent_with_decimal_in_preference():
    votes = parse_votes(_statement("statement_2022_06_15.html").voting_text)
    assert votes.for_count == 10
    assert [d["name"] for d in votes.against] == ["Esther L. George"]
    assert "by 0.5 percentage point" in votes.against[0]["preferred"]


def test_votes_tally_preface_unanimous():
    votes = parse_votes(_statement("statement_2026_09_16.html").voting_text)
    assert votes.for_count == 12
    assert votes.against == []


def test_votes_tally_preface_with_three_dissents():
    votes = parse_votes(_statement("statement_2026_07_29.html").voting_text)
    assert votes.for_count == 9
    assert [d["name"] for d in votes.against] == ["Beth M. Hammack", "Neel Kashkari", "Lorie K. Logan"]
    assert all("raise the target range" in d["preferred"] for d in votes.against)


def test_votes_empty_text_returns_zero_and_no_dissents():
    votes = parse_votes("")
    assert votes.for_count == 0
    assert votes.against == []


# -- minutes ----------------------------------------------------------------------


def test_extract_minutes_real_page():
    text = extract_minutes((FIXTURES / "minutes_2026_07_29.html").read_text())
    assert len(text.split()) > 3000
    assert "Developments in Financial Markets and Open Market Operations" in text
    assert "Return to text" not in text


def test_extract_minutes_missing_article():
    assert extract_minutes("<html><body></body></html>") == ""
