"""agents.macro.fetch_fed against the real RSS feed and calendar page saved in
tests/fixtures/fomc/. Network functions run through a real agents_core.http.Http with
an httpx.MockTransport — no live network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
from agents_core.http import Http

from agents.macro.config import FomcMeeting, load_fomc_calendar
from agents.macro.fetch_fed import (
    compare_calendars,
    fetch_feed,
    fetch_page,
    is_minutes_item,
    is_statement_item,
    minutes_items,
    new_statement_items,
    parse_calendar,
    parse_feed,
    parse_minutes_meeting_date,
    parse_statement_date_from_url,
    statement_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fomc"


@pytest.fixture
def items():
    return parse_feed((FIXTURES / "press_monetary.xml").read_text())


def _http(tmp_path, handler) -> Http:
    return Http(cache_dir=tmp_path / "cache", transport=httpx.MockTransport(handler))


def test_is_statement_item():
    assert is_statement_item("Federal Reserve issues FOMC statement ")
    assert not is_statement_item("Minutes of the Federal Open Market Committee, July 28–29, 2026")


def test_is_minutes_item_excludes_discount_rate_minutes():
    assert is_minutes_item("Minutes of the Federal Open Market Committee, July 28–29, 2026")
    assert not is_minutes_item("Minutes of the Board's discount rate meetings on June 8 and June 17, 2026")


def test_parse_statement_date_from_url():
    url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
    assert parse_statement_date_from_url(url) == date(2026, 9, 16)
    assert statement_url(date(2026, 9, 16)) == url
    with pytest.raises(ValueError):
        parse_statement_date_from_url("https://www.federalreserve.gov/not-a-statement.htm")


def test_parse_feed_real(items):
    assert len(items) == 15
    assert all(i.link.startswith("https://www.federalreserve.gov/") for i in items)


def test_new_statement_items_real_feed(items):
    statements = new_statement_items(items, since=None)
    assert [parse_statement_date_from_url(i.link) for i in statements] == [
        date(2026, 4, 29), date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
    ]  # fmt: skip
    newer = new_statement_items(items, since=date(2026, 7, 29))
    assert [parse_statement_date_from_url(i.link) for i in newer] == [date(2026, 9, 16)]
    assert new_statement_items(items, since=date(2026, 9, 16)) == []


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Minutes of the Federal Open Market Committee, July 28–29, 2026", date(2026, 7, 29)),
        ("Minutes of the Federal Open Market Committee, June 16-17, 2026", date(2026, 6, 17)),
        ("Minutes of the Federal Open Market Committee, April 30-May 1, 2024", date(2024, 5, 1)),
        ("Minutes of the Federal Open Market Committee, March 15, 2020", date(2020, 3, 15)),
        ("Minutes of the Federal Open Market Committee", None),
    ],
)
def test_parse_minutes_meeting_date(title, expected):
    assert parse_minutes_meeting_date(title) == expected


def test_minutes_items_real_feed(items):
    minutes = minutes_items(items)
    latest = minutes[-1]
    assert latest.meeting_date == date(2026, 7, 29)
    assert latest.released_at == date(2026, 8, 19)
    assert latest.url == "https://www.federalreserve.gov/monetarypolicy/fomcminutes20260729.htm"
    assert len(minutes) == 4  # the Board's discount-rate minutes are excluded


def test_parse_calendar_real_page():
    meetings = parse_calendar((FIXTURES / "fomccalendars.html").read_text())
    by_end = {m.end: m for m in meetings}
    assert by_end[date(2026, 9, 16)] == FomcMeeting(start=date(2026, 9, 15), end=date(2026, 9, 16), sep=True)
    assert by_end[date(2026, 10, 28)].sep is False
    assert date(2025, 8, 22) not in by_end  # notation vote, not a meeting
    assert len([m for m in meetings if m.start.year == 2026]) == 8


def test_parse_calendar_cross_month_meeting():
    html = (
        '<div class="panel"><div class="panel-heading"><h4>2024 FOMC Meetings</h4></div>'
        '<div class="row fomc-meeting"><div class="fomc-meeting__month">Apr/May</div>'
        '<div class="fomc-meeting__date">30-1</div></div></div>'
    )
    assert parse_calendar(html) == [FomcMeeting(start=date(2024, 4, 30), end=date(2024, 5, 1), sep=False)]


def test_fomc_dates_toml_matches_live_calendar():
    live = parse_calendar((FIXTURES / "fomccalendars.html").read_text())
    assert compare_calendars(live, load_fomc_calendar().meetings, since=date(2026, 1, 1)) == []


def test_compare_calendars_reports_disagreement():
    live = [FomcMeeting(start=date(2026, 10, 27), end=date(2026, 10, 28))]
    local = [FomcMeeting(start=date(2026, 10, 28), end=date(2026, 10, 29))]
    assert len(compare_calendars(live, local, since=date(2026, 10, 1))) == 2


def test_fetch_feed_never_cached(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=(FIXTURES / "press_monetary.xml").read_bytes())

    with _http(tmp_path, handler) as http:
        assert len(fetch_feed(http)) == 15
        fetch_feed(http)
    assert len(calls) == 2


def test_fetch_page_cached_permanently(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="<html>statement</html>")

    url = statement_url(date(2026, 9, 16))
    with _http(tmp_path, handler) as http:
        assert fetch_page(http, url) == "<html>statement</html>"
        fetch_page(http, url)
    assert calls == [url]
