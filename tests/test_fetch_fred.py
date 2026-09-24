"""Tests for agents.macro.fetch_fred — all HTTP is mocked with respx from
recorded-shape fixtures under tests/fixtures/fred/. No live network calls.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from agents.macro.fetch_fred import (
    FRED_BASE,
    fetch_observations,
    fetch_release_dates,
    fetch_release_id,
    fetch_series_meta,
    has_series_changed,
    parse_observation_value,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fred"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


def test_parse_observation_value_handles_missing_dot():
    assert parse_observation_value(".") is None


def test_parse_observation_value_parses_float():
    assert parse_observation_value("322.5") == 322.5


def test_parse_observation_value_parses_negative():
    assert parse_observation_value("-0.3") == -0.3


@respx.mock
def test_fetch_series_meta(client):
    respx.get(f"{FRED_BASE}/series").mock(
        return_value=httpx.Response(200, json=_load("series_cpiaucsl.json"))
    )
    meta = fetch_series_meta(client.get, "CPIAUCSL", api_key="fake-key")
    assert meta.series_id == "CPIAUCSL"
    assert meta.frequency == "Monthly"
    assert meta.last_updated == "2026-09-11 07:47:03-05"
    assert "Consumer Price Index" in meta.title


@respx.mock
def test_fetch_series_meta_sends_api_key_and_json_file_type(client):
    route = respx.get(f"{FRED_BASE}/series").mock(
        return_value=httpx.Response(200, json=_load("series_cpiaucsl.json"))
    )
    fetch_series_meta(client.get, "CPIAUCSL", api_key="my-key")
    request = route.calls.last.request
    assert request.url.params["api_key"] == "my-key"
    assert request.url.params["file_type"] == "json"
    assert request.url.params["series_id"] == "CPIAUCSL"


@respx.mock
def test_fetch_observations_parses_dates_and_missing_values(client):
    respx.get(f"{FRED_BASE}/series/observations").mock(
        return_value=httpx.Response(200, json=_load("observations_cpiaucsl.json"))
    )
    obs = fetch_observations(client.get, "CPIAUCSL", api_key="fake-key")
    assert len(obs) == 3
    assert obs[0].date == date(2026, 6, 1)
    assert obs[0].value == 320.1
    assert obs[1].value == 321.3
    assert obs[2].value is None  # "." parsed to None, never imputed


@respx.mock
def test_fetch_observations_passes_observation_start(client):
    route = respx.get(f"{FRED_BASE}/series/observations").mock(
        return_value=httpx.Response(200, json=_load("observations_cpiaucsl.json"))
    )
    fetch_observations(client.get, "CPIAUCSL", api_key="fake-key", observation_start="2026-06-01")
    request = route.calls.last.request
    assert request.url.params["observation_start"] == "2026-06-01"


@respx.mock
def test_fetch_release_id(client):
    respx.get(f"{FRED_BASE}/series/release").mock(
        return_value=httpx.Response(200, json=_load("series_release_cpiaucsl.json"))
    )
    release_id = fetch_release_id(client.get, "CPIAUCSL", api_key="fake-key")
    assert release_id == "10"


@respx.mock
def test_fetch_release_dates_returns_future_dates(client):
    respx.get(f"{FRED_BASE}/release/dates").mock(
        return_value=httpx.Response(200, json=_load("release_dates_10.json"))
    )
    dates = fetch_release_dates(client.get, "10", api_key="fake-key", realtime_start="2026-09-24")
    assert dates == [date(2026, 9, 11), date(2026, 10, 14), date(2026, 11, 13)]


@respx.mock
def test_fetch_release_dates_requests_no_data_flag(client):
    route = respx.get(f"{FRED_BASE}/release/dates").mock(
        return_value=httpx.Response(200, json=_load("release_dates_10.json"))
    )
    fetch_release_dates(client.get, "10", api_key="fake-key", realtime_start="2026-09-24")
    request = route.calls.last.request
    assert request.url.params["include_release_dates_with_no_data"] == "true"
    assert request.url.params["realtime_start"] == "2026-09-24"


def test_has_series_changed_true_when_never_stored():
    assert has_series_changed(None, "2026-09-11 07:47:03-05") is True


def test_has_series_changed_false_when_identical():
    assert has_series_changed("2026-09-11 07:47:03-05", "2026-09-11 07:47:03-05") is False


def test_has_series_changed_true_when_different():
    assert has_series_changed("2026-08-13 07:47:03-05", "2026-09-11 07:47:03-05") is True
