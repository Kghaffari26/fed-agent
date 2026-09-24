"""Tests for agents.macro.fetch_fed's pure RSS parsing logic. The one network
call (`fetch_feed`) is exercised with a fake client against a fixture RSS
file — no live network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from agents.macro.fetch_fed import (
    FeedItem,
    fetch_feed,
    is_minutes_item,
    is_statement_item,
    new_statement_items,
    parse_feed,
    parse_statement_date_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fomc"


@dataclass
class _FakeResponse:
    text: str


def _fake_get(url: str) -> _FakeResponse:
    return _FakeResponse(text=(FIXTURES / "press_monetary.xml").read_text())


def test_is_statement_item_matches_exact_title():
    assert is_statement_item("Federal Reserve issues FOMC statement") is True
    assert is_statement_item("Federal Reserve issues FOMC statement ") is True  # trailing space


def test_is_statement_item_rejects_other_titles():
    assert is_statement_item("Minutes of the Federal Open Market Committee, July 2026") is False


def test_is_minutes_item_matches_prefix():
    assert is_minutes_item("Minutes of the Federal Open Market Committee, July 28-29, 2026") is True


def test_is_minutes_item_rejects_statement():
    assert is_minutes_item("Federal Reserve issues FOMC statement") is False


def test_parse_statement_date_from_url():
    url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
    assert parse_statement_date_from_url(url) == date(2026, 9, 16)


def test_parse_statement_date_from_url_invalid_raises():
    try:
        parse_statement_date_from_url("https://www.federalreserve.gov/not-a-statement.htm")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_parse_feed_extracts_all_items():
    items = parse_feed((FIXTURES / "press_monetary.xml").read_text())
    assert len(items) == 4
    assert all(isinstance(i, FeedItem) for i in items)


def test_new_statement_items_filters_to_statements_only():
    items = parse_feed((FIXTURES / "press_monetary.xml").read_text())
    statements = new_statement_items(items, since=None)
    assert len(statements) == 3
    assert all(is_statement_item(i.title) for i in statements)


def test_new_statement_items_filters_by_since_date():
    items = parse_feed((FIXTURES / "press_monetary.xml").read_text())
    statements = new_statement_items(items, since=date(2026, 7, 29))
    assert len(statements) == 1
    assert parse_statement_date_from_url(statements[0].link) == date(2026, 9, 16)


def test_new_statement_items_sorted_oldest_first():
    items = parse_feed((FIXTURES / "press_monetary.xml").read_text())
    statements = new_statement_items(items, since=None)
    dates = [parse_statement_date_from_url(i.link) for i in statements]
    assert dates == sorted(dates)


def test_fetch_feed_uses_injected_get():
    items = fetch_feed(_fake_get, url="https://example.invalid/feed.xml")
    assert len(items) == 4
