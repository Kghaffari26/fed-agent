"""Federal Reserve website: RSS discovery, statement/minutes fetch, meeting calendar.

See docs/specs/SPEC_MACRO.md §3 and §5.7 step 1. All network access goes through
`agents_core.http.Http` (retries, the 1 req/s politeness limit set in
`agent.MacroAgent.configure_http`, the on-disk cache). Everything else here is pure
parsing, tested against real pages saved in tests/fixtures/fomc/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
from agents_core.http import Http
from selectolax.parser import HTMLParser

from agents.macro.config import FomcMeeting

FED_HOST = "www.federalreserve.gov"
FOMC_RSS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
MINUTES_URL = "https://www.federalreserve.gov/monetarypolicy/fomcminutes{date:%Y%m%d}.htm"
STATEMENT_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary{date:%Y%m%d}a.htm"
STATEMENT_TITLE = "Federal Reserve issues FOMC statement"
MINUTES_TITLE_PREFIX = "Minutes of the Federal Open Market Committee"

# §3: statements and minutes never change once published, so cache them for good.
PERMANENT_TTL_SECONDS = 10 * 365 * 24 * 3600

_STATEMENT_URL_DATE_RE = re.compile(r"monetary(\d{4})(\d{2})(\d{2})a\.htm")
_MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}  # fmt: skip
# "Minutes of the Federal Open Market Committee, July 28–29, 2026" (also "April 30-May 1, 2024").
_MINUTES_TITLE_RE = re.compile(
    r"Committee,\s+([A-Za-z]+)\s+(\d{1,2})(?:\s*[–—-]\s*(?:([A-Za-z]+)\s+)?(\d{1,2}))?,\s+(\d{4})"
)
_CALENDAR_YEAR_RE = re.compile(r"(\d{4}) FOMC Meetings")


@dataclass
class FeedItem:
    title: str
    link: str
    published: str

    @property
    def published_date(self) -> date | None:
        try:
            return parsedate_to_datetime(self.published).date()
        except (TypeError, ValueError):
            return None


@dataclass
class MinutesItem:
    meeting_date: date  # the meeting's final day
    released_at: date
    url: str


def is_statement_item(title: str) -> bool:
    return title.strip() == STATEMENT_TITLE


def is_minutes_item(title: str) -> bool:
    return title.strip().startswith(MINUTES_TITLE_PREFIX)


def parse_statement_date_from_url(url: str) -> date:
    """URLs look like .../monetary20260916a.htm."""
    match = _STATEMENT_URL_DATE_RE.search(url)
    if not match:
        raise ValueError(f"could not parse a statement date from url: {url!r}")
    year, month, day = (int(g) for g in match.groups())
    return date(year, month, day)


def statement_url(day: date) -> str:
    return STATEMENT_URL.format(date=day)


def parse_feed(xml_text: str) -> list[FeedItem]:
    parsed = feedparser.parse(xml_text)
    return [
        FeedItem(
            title=e.get("title", "").strip(),
            link=e.get("link", "").strip(),
            published=e.get("published", ""),
        )
        for e in parsed.entries
    ]


def statement_items(items: list[FeedItem]) -> list[FeedItem]:
    """Every statement item with a parseable URL date, sorted oldest-first."""
    out = []
    for item in items:
        if not is_statement_item(item.title):
            continue
        try:
            parse_statement_date_from_url(item.link)
        except ValueError:
            continue
        out.append(item)
    out.sort(key=lambda i: parse_statement_date_from_url(i.link))
    return out


def new_statement_items(items: list[FeedItem], *, since: date | None) -> list[FeedItem]:
    """Statement items newer than `since` (§5.7 step 1), sorted oldest-first."""
    return [
        i for i in statement_items(items) if since is None or parse_statement_date_from_url(i.link) > since
    ]


def parse_minutes_meeting_date(title: str) -> date | None:
    """The meeting's final day from a minutes item title, or None if unparseable."""
    match = _MINUTES_TITLE_RE.search(title)
    if not match:
        return None
    month1, day1, month2, day2, year = match.groups()
    month = _MONTHS.get((month2 or month1).lower())
    if month is None:
        return None
    return date(int(year), month, int(day2 or day1))


def minutes_items(items: list[FeedItem]) -> list[MinutesItem]:
    """FOMC minutes items (not the Board's discount-rate minutes), oldest meeting first.

    The URL is built from the meeting date (`fomcminutesYYYYMMDD.htm`) rather than
    fetched from the press release page, saving a request per item.
    """
    out = []
    for item in items:
        if not is_minutes_item(item.title):
            continue
        meeting = parse_minutes_meeting_date(item.title)
        released = item.published_date
        if meeting is None or released is None:
            continue
        out.append(
            MinutesItem(meeting_date=meeting, released_at=released, url=MINUTES_URL.format(date=meeting))
        )
    out.sort(key=lambda m: m.meeting_date)
    return out


def parse_calendar(html: str) -> list[FomcMeeting]:
    """Scheduled meetings from fomccalendars.htm. Notation votes and unscheduled
    meetings (a parenthetical in the date cell) are skipped; `*` marks an SEP meeting."""
    tree = HTMLParser(html)
    meetings: list[FomcMeeting] = []
    for panel in tree.css("div.panel"):
        heading = panel.css_first(".panel-heading")
        year_match = _CALENDAR_YEAR_RE.search(heading.text() if heading else "")
        if not year_match:
            continue
        year = int(year_match.group(1))
        for row in panel.css("div.fomc-meeting"):
            month_node = row.css_first(".fomc-meeting__month")
            date_node = row.css_first(".fomc-meeting__date")
            if month_node is None or date_node is None:
                continue
            month_text = month_node.text(strip=True)
            date_text = date_node.text(strip=True)
            if "(" in date_text:
                continue
            sep = "*" in date_text
            days = re.findall(r"\d{1,2}", date_text)
            months = [_MONTHS.get(m.strip().lower()) for m in month_text.split("/")]
            if not days or None in months:
                continue
            start_month, end_month = months[0], months[-1]
            start_day, end_day = int(days[0]), int(days[-1])
            try:
                start = date(year, start_month, start_day)
                end = date(year, end_month, end_day)
            except ValueError:
                continue
            meetings.append(FomcMeeting(start=start, end=end, sep=sep))
    meetings.sort(key=lambda m: m.start)
    return meetings


# ---- network (through agents_core.http) -------------------------------------------


def download_text(http: Http, url: str, dest: Path) -> str:
    """A conditional GET (agents-core's `Http.download`: ETag/Last-Modified sidecar).
    Always asks the server, so a new statement is never hidden by a TTL; a 304 reuses
    the previous copy in `dest` without re-downloading it."""
    return http.download(url, dest).path.read_text(encoding="utf-8")


def fetch_feed(http: Http, dest: Path, url: str = FOMC_RSS_URL) -> list[FeedItem]:
    # The feed is how a new statement is discovered: checked on every run.
    return parse_feed(download_text(http, url, dest))


def fetch_page(http: Http, url: str) -> str:
    """A statement or minutes page, cached permanently (§3)."""
    return http.get(url, ttl_seconds=PERMANENT_TTL_SECONDS).text


def fetch_calendar(http: Http, dest: Path) -> list[FomcMeeting]:
    return parse_calendar(download_text(http, CALENDAR_URL, dest))


def compare_calendars(parsed: list[FomcMeeting], fallback: list[FomcMeeting], *, since: date) -> list[str]:
    """Human-readable disagreements between the live calendar and config/fomc_dates.toml
    for meetings ending on/after `since` (§8: log a warning when they differ)."""

    def key(m: FomcMeeting) -> tuple[date, date, bool]:
        return (m.start, m.end, m.sep)

    live = {key(m) for m in parsed if m.end >= since}
    local = {key(m) for m in fallback if m.end >= since}
    if not live:
        return []
    # Only compare the span both sources cover.
    horizon = max(k[1] for k in local) if local else since
    live = {k for k in live if k[1] <= horizon}
    diffs = [f"calendar page has {k[0]}..{k[1]} sep={k[2]}, fomc_dates.toml does not" for k in live - local]
    diffs += [f"fomc_dates.toml has {k[0]}..{k[1]} sep={k[2]}, calendar page does not" for k in local - live]
    return sorted(diffs)
