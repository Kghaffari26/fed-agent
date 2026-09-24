"""Federal Reserve RSS discovery: parses statement/minutes items and their
dates. See docs/specs/SPEC_MACRO.md §3 and §5.7 step 1.

As with fetch_fred.py, the HTTP transport (politeness rate limit, permanent
statement cache) is deliberately not implemented here — that belongs to
`agents_core.http` per this repo's multi-repo rules. `fetch_feed` takes an
injected `get` callable; everything else is pure parsing, testable without
network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import feedparser

FOMC_RSS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
STATEMENT_TITLE = "Federal Reserve issues FOMC statement"
MINUTES_TITLE_PREFIX = "Minutes of the Federal Open Market Committee"

_STATEMENT_URL_DATE_RE = re.compile(r"monetary(\d{4})(\d{2})(\d{2})a\.htm")


class GetFn(Protocol):
    def __call__(self, url: str) -> HttpResponse: ...


class HttpResponse(Protocol):
    text: str


@dataclass
class FeedItem:
    title: str
    link: str
    published: str


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


def parse_feed(xml_text: str) -> list[FeedItem]:
    parsed = feedparser.parse(xml_text)
    return [
        FeedItem(title=e.get("title", ""), link=e.get("link", ""), published=e.get("published", ""))
        for e in parsed.entries
    ]


def new_statement_items(items: list[FeedItem], *, since: date | None) -> list[FeedItem]:
    """Statement items newer than `since` (§5.7 step 1), sorted oldest-first."""
    out = []
    for item in items:
        if not is_statement_item(item.title):
            continue
        try:
            item_date = parse_statement_date_from_url(item.link)
        except ValueError:
            continue
        if since is None or item_date > since:
            out.append(item)
    out.sort(key=lambda i: parse_statement_date_from_url(i.link))
    return out


def new_minutes_items(items: list[FeedItem], *, since: date | None) -> list[FeedItem]:
    out = [item for item in items if is_minutes_item(item.title)]
    if since is not None:
        # Minutes links don't carry a parseable date the way statement links
        # do; callers filter by the meeting date recovered from the minutes
        # page itself. Here we can only pass through candidates.
        pass
    return out


def fetch_feed(get: GetFn, url: str = FOMC_RSS_URL) -> list[FeedItem]:
    response = get(url)
    return parse_feed(response.text)
