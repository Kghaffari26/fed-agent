"""FRED fetch: series metadata, observations, release dates, change detection.

See docs/specs/SPEC_MACRO.md §3 and §5.5.

The HTTP transport itself (retries, the 2 req/s rate limit, the on-disk cache)
is deliberately NOT implemented here — per this repo's multi-repo rules that
belongs to `agents_core.http`, shared by every agent. Every function below
takes a `get` callable with the same shape as `httpx.Client.get` (or
`agents_core.http`'s eventual wrapper) so the runner can inject the real
transport once `agents-core` is installable; tests inject a fake client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

FRED_BASE = "https://api.stlouisfed.org/fred"


class GetFn(Protocol):
    def __call__(self, url: str, *, params: dict[str, str]) -> HttpResponse: ...


class HttpResponse(Protocol):
    def json(self) -> dict: ...


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


def _params(api_key: str, **extra: str) -> dict[str, str]:
    params = {"api_key": api_key, "file_type": "json"}
    params.update(extra)
    return params


def parse_observation_value(raw: str) -> float | None:
    """FRED encodes missing values as the literal string '.'."""
    if raw == ".":
        return None
    return float(raw)


def fetch_series_meta(get: GetFn, series_id: str, api_key: str) -> SeriesMeta:
    resp = get(f"{FRED_BASE}/series", params=_params(api_key, series_id=series_id))
    series = resp.json()["seriess"][0]
    return SeriesMeta(
        series_id=series["id"],
        title=series["title"],
        units=series["units"],
        frequency=series["frequency"],
        last_updated=series["last_updated"],
    )


def fetch_observations(
    get: GetFn, series_id: str, api_key: str, *, observation_start: str | None = None
) -> list[Observation]:
    extra = {"series_id": series_id}
    if observation_start:
        extra["observation_start"] = observation_start
    resp = get(f"{FRED_BASE}/series/observations", params=_params(api_key, **extra))
    out = []
    for row in resp.json()["observations"]:
        out.append(
            Observation(
                date=date.fromisoformat(row["date"]),
                value=parse_observation_value(row["value"]),
            )
        )
    return out


def fetch_release_id(get: GetFn, series_id: str, api_key: str) -> str:
    resp = get(f"{FRED_BASE}/series/release", params=_params(api_key, series_id=series_id))
    return str(resp.json()["releases"][0]["id"])


def fetch_release_dates(
    get: GetFn, release_id: str, api_key: str, *, realtime_start: str
) -> list[date]:
    """Future scheduled release dates for `release_id` (§3, powers the calendar)."""
    resp = get(
        f"{FRED_BASE}/release/dates",
        params=_params(
            api_key,
            release_id=release_id,
            include_release_dates_with_no_data="true",
            realtime_start=realtime_start,
        ),
    )
    return [date.fromisoformat(row["date"]) for row in resp.json()["release_dates"]]


def has_series_changed(stored_last_updated: str | None, new_last_updated: str) -> bool:
    """§3 change detection: skip the observations call when last_updated is unchanged."""
    return stored_last_updated != new_last_updated
