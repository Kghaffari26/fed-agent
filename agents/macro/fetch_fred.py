"""FRED fetch: series metadata, observations, release lookups, change detection.

See docs/specs/SPEC_MACRO.md §3 and §5.5. All requests go through
`agents_core.http.Http`, which owns retries/backoff, the per-host rate limit
(configured in `agent.MacroAgent.configure_http`) and the on-disk cache, and never
writes the `api_key` query param to its cache or logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from agents_core.http import Http

FRED_HOST = "api.stlouisfed.org"
FRED_BASE = "https://api.stlouisfed.org/fred"
RELEASE_DATES_TTL_SECONDS = 12 * 3600
# §3: a series' release is cached for 30 days. agents-core's HTTP cache stores 2xx
# responses only (v0.2.0), and CI keeps .cache/ between runs, so it lives there.
RELEASE_TTL_SECONDS = 30 * 24 * 3600


@dataclass
class SeriesMeta:
    series_id: str
    title: str
    units: str
    frequency: str
    last_updated: str


@dataclass
class Observation:
    date: date
    value: float | None


@dataclass
class ReleaseInfo:
    release_id: str
    name: str


def _params(api_key: str, **extra: str) -> dict[str, str]:
    params = {"api_key": api_key, "file_type": "json"}
    params.update(extra)
    return params


def parse_observation_value(raw: str) -> float | None:
    """FRED encodes missing values as the literal string '.'."""
    if raw == ".":
        return None
    return float(raw)


def fetch_series_meta(http: Http, series_id: str, api_key: str) -> SeriesMeta:
    # Never cached: last_updated is the change detector (§3).
    resp = http.get(f"{FRED_BASE}/series", params=_params(api_key, series_id=series_id), ttl_seconds=0)
    series = resp.json()["seriess"][0]
    return SeriesMeta(
        series_id=series["id"],
        title=series["title"],
        units=series["units"],
        frequency=series["frequency"],
        last_updated=series["last_updated"],
    )


def fetch_observations(
    http: Http, series_id: str, api_key: str, *, observation_start: str | None = None
) -> list[Observation]:
    extra = {"series_id": series_id}
    if observation_start:
        extra["observation_start"] = observation_start
    resp = http.get(f"{FRED_BASE}/series/observations", params=_params(api_key, **extra), ttl_seconds=0)
    return [
        Observation(date=date.fromisoformat(row["date"]), value=parse_observation_value(row["value"]))
        for row in resp.json()["observations"]
    ]


def fetch_release(http: Http, series_id: str, api_key: str) -> ReleaseInfo:
    """The release a series belongs to (§3: cached 30 days)."""
    resp = http.get(
        f"{FRED_BASE}/series/release",
        params=_params(api_key, series_id=series_id),
        ttl_seconds=RELEASE_TTL_SECONDS,
    )
    release = resp.json()["releases"][0]
    return ReleaseInfo(release_id=str(release["id"]), name=release["name"])


def fetch_release_id(http: Http, series_id: str, api_key: str) -> str:
    return fetch_release(http, series_id, api_key).release_id


def fetch_release_dates(
    http: Http, release_id: str, api_key: str, *, realtime_start: str, published_only: bool = False
) -> list[date]:
    """Release dates on/after `realtime_start`.

    By default FRED's full calendar, including scheduled dates with no data yet (§3,
    powers the calendar). With `published_only=True`, only the dates on which FRED
    actually published data for the release — its real release status, which §5.5's
    delayed check needs: a past scheduled date that isn't in this list hasn't been
    published; one that is was published, even if a given series didn't change.
    """
    resp = http.get(
        f"{FRED_BASE}/release/dates",
        params=_params(
            api_key,
            release_id=release_id,
            include_release_dates_with_no_data="false" if published_only else "true",
            realtime_start=realtime_start,
        ),
        ttl_seconds=RELEASE_DATES_TTL_SECONDS,
    )
    return [date.fromisoformat(row["date"]) for row in resp.json()["release_dates"]]


def has_series_changed(stored_last_updated: str | None, new_last_updated: str) -> bool:
    """§3 change detection: skip the observations call when last_updated is unchanged."""
    return stored_last_updated != new_last_updated
