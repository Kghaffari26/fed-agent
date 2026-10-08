"""Test doubles for end-to-end agent runs: a fake FRED + federalreserve.gov served via
httpx.MockTransport (so the real agents_core.http.Http is exercised), and a fake
Anthropic client with the `messages.parse` surface agents_core.llm uses.

No live network, no real LLM calls.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from agents.macro.config import load_macro_config
from agents.macro.events import Event
from agents.macro.templates import headline_for_event

FOMC_FIXTURES = Path(__file__).parent / "fixtures" / "fomc"

# Page URL -> saved real page.
_FED = "https://www.federalreserve.gov"
FED_PAGES = {
    f"{_FED}/feeds/press_monetary.xml": "press_monetary.xml",
    f"{_FED}/monetarypolicy/fomccalendars.htm": "fomccalendars.html",
    f"{_FED}/monetarypolicy/fomcminutes20260729.htm": "minutes_2026_07_29.html",
    f"{_FED}/newsevents/pressreleases/monetary20260916a.htm": "statement_2026_09_16.html",
    f"{_FED}/newsevents/pressreleases/monetary20260729a.htm": "statement_2026_07_29.html",
    f"{_FED}/newsevents/pressreleases/monetary20260617a.htm": "statement_2026_06_17.html",
}

FIXED_LEVELS = {"DFEDTARU": 4.0, "DFEDTARL": 3.75}


def _dates(frequency: str, end: date, years: int = 12) -> list[date]:
    start = end.replace(year=end.year - years)
    out = []
    if frequency == "monthly":
        y, m = start.year, start.month
        while date(y, m, 1) <= end:
            out.append(date(y, m, 1))
            m += 1
            if m == 13:
                y, m = y + 1, 1
    elif frequency == "quarterly":
        y, m = start.year, 1
        while date(y, m, 1) <= end:
            out.append(date(y, m, 1))
            m += 3
            if m > 12:
                y, m = y + 1, 1
    elif frequency == "weekly":
        d = start
        while d <= end:
            out.append(d)
            d += timedelta(days=7)
    else:
        d = start
        while d <= end:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
    return out


@dataclass
class FakeFred:
    """Deterministic synthetic series for every configured indicator."""

    today: date
    last_updated: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, dict[date, float]] = field(default_factory=dict)
    series: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)
    reject_key: bool = False  # answer every request like FRED does for a bad api_key

    def __post_init__(self) -> None:
        config = load_macro_config()
        for i, ind in enumerate([*config.indicators, *config.components]):
            sid = ind.fred_series
            end = self.today - timedelta(days=40) if ind.frequency in ("monthly", "quarterly") else self.today
            points = []
            for k, d in enumerate(_dates(ind.frequency, end)):
                if sid in FIXED_LEVELS:
                    value = FIXED_LEVELS[sid]
                elif ind.primary == "mom_diff":  # payrolls and its sectors: steady growth
                    step = 120.0 if sid == "PAYEMS" else 60.0 - i
                    value = (150000.0 if sid == "PAYEMS" else 10000.0 + 100.0 * i) + step * k
                elif ind.primary in ("yoy_pct", "mom_pct", "ann_3m_pct"):
                    value = 100.0 * ((1.002 + 0.0001 * (i % 7)) ** k)
                else:
                    value = 3.0 + (i % 5) * 0.4 + 0.3 * math.sin(k / 9.0)
                points.append((d, round(value, 3)))
            self.series[sid] = points
            self.last_updated.setdefault(sid, "2026-09-01 08:00:00-05")

    def observations(self, sid: str, start: str | None) -> list[dict]:
        rows = []
        override = self.overrides.get(sid, {})
        for d, v in self.series[sid]:
            if start and d.isoformat() < start:
                continue
            rows.append({"date": d.isoformat(), "value": str(override.get(d, v))})
        return rows

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append(str(url.copy_remove_param("api_key")))
        if self.reject_key:
            message = "Bad Request.  The value for variable api_key is not registered."
            return httpx.Response(400, json={"error_code": 400, "error_message": message})
        params = url.params
        path = url.path.removeprefix("/fred")
        if path == "/series":
            sid = params["series_id"]
            return httpx.Response(
                200,
                json={
                    "seriess": [
                        {
                            "id": sid,
                            "title": sid,
                            "units": "u",
                            "frequency": "f",
                            "last_updated": self.last_updated[sid],
                        }
                    ]
                },
            )
        if path == "/series/observations":
            return httpx.Response(
                200,
                json={
                    "observations": self.observations(params["series_id"], params.get("observation_start"))
                },
            )
        if path == "/series/release":
            sid = params["series_id"]
            return httpx.Response(
                200, json={"releases": [{"id": sum(map(ord, sid)) % 1000, "name": f"{sid} release"}]}
            )
        if path == "/release/dates":
            rid = params["release_id"]
            dates = [self.today + timedelta(days=7 + int(rid) % 20)]
            if params.get("include_release_dates_with_no_data") == "false":
                dates = [d for d in dates if d <= self.today]
            return httpx.Response(
                200, json={"release_dates": [{"release_id": rid, "date": d.isoformat()} for d in dates]}
            )
        return httpx.Response(404)


def fake_transport(fred: FakeFred) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.stlouisfed.org":
            return fred.handle(request)
        name = FED_PAGES.get(str(request.url))
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, content=(FOMC_FIXTURES / name).read_bytes())

    return httpx.MockTransport(handler)


# ---- fake Anthropic client ------------------------------------------------------------


def good_brief(prompt: dict) -> dict:
    """Bullets rendered from the events' own facts: always passes the guard."""
    bullets = []
    for raw in prompt["events"][:5]:
        event = Event(id=raw["id"], type=raw["type"], priority=0, facts=raw["facts"])
        bullets.append({"text": headline_for_event(event), "event_ids": [raw["id"]]})
    return {"bullets": bullets}


def good_fomc_read(prompt: dict) -> dict:
    changes = prompt["changes"]
    first_sentence = prompt["latest_text"].split(". ")[0]
    return {
        "summary": "The Committee raised the target range and trimmed its description of the outlook.",
        "tone_shift": "more_hawkish" if changes else "unchanged",
        "rationale": "The decision sentence changed from maintaining to raising the range.",
        "cited_change_idx": [changes[0]["idx"]] if changes else [],
        "key_phrases": [
            {"phrase": first_sentence, "interpretation": "The policy action itself."},
            {"phrase": "a phrase that is not in the statement", "interpretation": "Should be dropped."},
        ],
    }


def good_minutes(prompt: dict) -> dict:
    return {"summary": "Participants discussed the outlook for inflation and the labor market."}


RESPONDERS: dict[str, Callable[[dict], dict]] = {
    "JudgeVerdict": lambda prompt: {"reasoning": "Specific and neutral.", "score": 4},
    "BriefDraft": good_brief,
    "FomcReadDraft": good_fomc_read,
    "MinutesDraft": good_minutes,
}


class FakeMessages:
    def __init__(self, responders: dict[str, Callable[[dict], dict]]):
        self.responders = responders
        self.calls: list[dict[str, Any]] = []

    def parse(self, *, output_format, **params):
        self.calls.append({"output_format": output_format.__name__, **params})
        content = params["messages"][0]["content"]
        try:
            prompt = json.loads(content)
        except json.JSONDecodeError:  # e.g. the eval LLM judge's rubric prompt
            prompt = {"text": content}
        payload = self.responders[output_format.__name__](prompt)
        return SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1500, output_tokens=250, cache_creation_input_tokens=0, cache_read_input_tokens=0
            ),
            stop_reason="end_turn",
            parsed_output=output_format.model_validate(payload),
            content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        )

    def create(self, **params):
        """The release investigator's tool-use loop (agents_core LLM.converse)."""
        self.calls.append({"output_format": "investigator", **params})
        blocks = self.investigator(params)
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=2500, output_tokens=150, cache_creation_input_tokens=0, cache_read_input_tokens=0
            ),
            stop_reason=stop,
            stop_details=None,
            content=[SimpleNamespace(**b) for b in blocks],
        )


# ---- scripted release investigator ------------------------------------------------------


def message_text(message: dict) -> str:
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


def tool_results(messages: list[dict]) -> dict[str, Any]:
    """tool name -> parsed JSON output, from every tool_result so far."""
    names = {}
    for m in messages:
        if m["role"] == "assistant":
            for b in m["content"]:
                if b.get("type") == "tool_use":
                    names[b["id"]] = b["name"]
    out: dict[str, Any] = {}
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result" and not b.get("is_error"):
                    body = b["content"]
                    if isinstance(body, list):
                        body = "".join(x.get("text", "") for x in body)
                    body = body[body.index(">") + 1 : body.rindex("</")]
                    out[names[b["tool_use_id"]]] = json.loads(body)
    return out


def good_investigator(params: dict, *, invent_number: bool = False) -> list[dict]:
    """Step 1: get_series on the trigger (core_pce for an FOMC day) + get_fomc_context.
    Step 2: finish, quoting only the latest value get_series returned."""
    messages = params["messages"]
    task = json.loads(message_text(messages[0]))
    series_id = task["trigger"]["series_id"] or "core_pce"
    if len(messages) == 1:
        return [
            {"type": "text", "text": "Looking at the series."},
            {"type": "tool_use", "id": "t1", "name": "get_series", "input": {"id": series_id, "range": "2y"}},
            {"type": "tool_use", "id": "t2", "name": "get_fomc_context", "input": {}},
        ]
    got = tool_results(messages)["get_series"]
    value = 987.6 if invent_number else got["latest"][1]
    return [
        {
            "type": "tool_use",
            "id": f"f{len(messages)}",
            "name": "finish",
            "input": {
                "analysis": f"{got['name']} was {value} in the latest reading.",
                "cited_series": [series_id, "not_a_series"],
            },
        }
    ]


class FakeAnthropic:
    def __init__(
        self,
        responders: dict[str, Callable[[dict], dict]] | None = None,
        investigator: Callable[[dict], list[dict]] = good_investigator,
    ):
        self.messages = FakeMessages({**RESPONDERS, **(responders or {})})
        self.messages.investigator = investigator

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.messages.calls
