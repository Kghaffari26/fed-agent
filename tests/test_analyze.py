"""§7 analyze.py: prompt building, citation attachment, tone-shift validation, verbatim
phrase filtering, and the guarded calls through a real agents_core.llm.LLM with a fake
Anthropic client (no network)."""

from __future__ import annotations

from datetime import date

import pytest
from agents_core.costs import CostTracker
from agents_core.llm import LLM

from agents.macro.analyze import (
    WhatChangedInput,
    attach_citations,
    build_fomc_read_prompt,
    build_what_changed_prompt,
    complete_sentences,
    filter_verbatim_key_phrases,
    generate_brief,
    generate_fomc_read,
    text_numbers,
    validate_tone_shift,
)
from agents.macro.events import new_release_event
from agents.macro.fomc import Change
from agents.macro.schema import FomcChange, FomcLatest, FomcTargetRange, FomcVotes
from tests.macro_fakes import FakeAnthropic


def test_build_what_changed_prompt_shape():
    events = [new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9})]
    payload = WhatChangedInput(as_of="2026-09-11", events=events, context={"regimes": {}})
    prompt = build_what_changed_prompt(payload)
    assert prompt["as_of"] == "2026-09-11"
    assert prompt["events"][0]["id"] == events[0].id
    assert prompt["events"][0]["facts"] == {"yoy": 2.9}
    assert prompt["context"] == {"regimes": {}}


def test_attach_citations_pulls_source_url_from_event_id():
    events = [new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9})]
    event_by_id = {e.id: e for e in events}
    bullets = [{"text": "CPI rose 2.9% YoY.", "event_ids": [events[0].id]}]
    result = attach_citations(bullets, event_by_id, {"cpi": "https://fred.stlouisfed.org/series/CPIAUCSL"})
    assert len(result) == 1
    assert result[0].citations[0].url == "https://fred.stlouisfed.org/series/CPIAUCSL"
    assert result[0].citations[0].name == "FRED: cpi"


def test_attach_citations_dedupes_repeated_urls():
    e1 = new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9})
    e2 = new_release_event("cpi", period=date(2026, 7, 1), high_priority=True, facts={"yoy": 2.7})
    event_by_id = {e1.id: e1, e2.id: e2}
    bullets = [{"text": "CPI has been steady.", "event_ids": [e1.id, e2.id]}]
    result = attach_citations(bullets, event_by_id, {"cpi": "https://fred.stlouisfed.org/series/CPIAUCSL"})
    assert len(result[0].citations) == 1


def test_attach_citations_skips_unknown_event_ids():
    bullets = [{"text": "Something happened.", "event_ids": ["not_a_real_event"]}]
    result = attach_citations(bullets, {}, {})
    assert result[0].citations == []


def test_model_never_writes_urls_only_code_does():
    # the model's bullet dict has no "citations" key at all -- code adds them
    events = [new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9})]
    event_by_id = {e.id: e for e in events}
    bullets = [{"text": "CPI rose.", "event_ids": [events[0].id]}]
    assert "citations" not in bullets[0]
    result = attach_citations(bullets, event_by_id, {"cpi": "https://fred.stlouisfed.org/series/CPIAUCSL"})
    assert len(result[0].citations) == 1


# -- FOMC read prompt -----------------------------------------------------------


def test_build_fomc_read_prompt_shape():
    changes = [Change(idx=0, type="modified", before="old text", after="new text")]
    prompt = build_fomc_read_prompt(
        decision="cut", change_bp=-25, votes={"for_count": 10, "against": []}, changes=changes
    )
    assert prompt["decision"] == "cut"
    assert prompt["change_bp"] == -25
    assert prompt["changes"] == [{"idx": 0, "type": "modified", "before": "old text", "after": "new text"}]


# -- tone-shift validation (§7.3 rule 1) -----------------------------------------


def test_tone_shift_valid_with_cited_changes():
    changes = [Change(idx=0, type="modified", before="a", after="b")]
    assert validate_tone_shift("more_dovish", [0], changes) is True


def test_tone_shift_invalid_without_citation_when_changes_exist():
    changes = [Change(idx=0, type="modified", before="a", after="b")]
    assert validate_tone_shift("more_dovish", [], changes) is False


def test_tone_shift_empty_diff_must_be_unchanged():
    assert validate_tone_shift("unchanged", [], []) is True
    assert validate_tone_shift("more_hawkish", [], []) is False


# -- verbatim key-phrase filtering (§7.3 rule 2) ---------------------------------


def test_filter_verbatim_key_phrases_keeps_matching():
    latest_text = "Job gains have slowed in recent months."
    phrases = [{"phrase": "job gains have slowed", "interpretation": "labor cooling"}]
    kept, rate = filter_verbatim_key_phrases(phrases, latest_text)
    # exact-substring match is case-sensitive per spec ("appear verbatim")
    assert kept == []
    assert rate == 0.0


def test_filter_verbatim_key_phrases_case_sensitive_exact_match():
    latest_text = "Job gains have slowed in recent months."
    phrases = [{"phrase": "Job gains have slowed", "interpretation": "labor cooling"}]
    kept, rate = filter_verbatim_key_phrases(phrases, latest_text)
    assert len(kept) == 1
    assert rate == 1.0


def test_filter_verbatim_key_phrases_drops_non_matching():
    latest_text = "Job gains have slowed in recent months."
    phrases = [
        {"phrase": "Job gains have slowed", "interpretation": "labor cooling"},
        {"phrase": "inflation is shockingly high", "interpretation": "fabricated"},
    ]
    kept, rate = filter_verbatim_key_phrases(phrases, latest_text)
    assert len(kept) == 1
    assert kept[0].phrase == "Job gains have slowed"
    assert rate == 0.5


def test_filter_verbatim_key_phrases_empty_list():
    kept, rate = filter_verbatim_key_phrases([], "any text")
    assert kept == []
    assert rate == 1.0


# -- guarded generation through agents_core.llm (fake client) ---------------------------


@pytest.fixture
def llm_for(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS_CORE_DATA_DIR", str(tmp_path))

    def make(responders):
        client = FakeAnthropic(responders)
        return LLM(
            CostTracker(agent="macro", run_id="t", path=tmp_path / "costs.jsonl"), client=client
        ), client

    return make


def test_complete_sentences_trims_trailing_fragment():
    assert complete_sentences("One. Two is cut off and") == "One."
    assert complete_sentences("Done.") == "Done."
    assert complete_sentences("No stop at all") == "No stop at all"


def test_text_numbers_reads_fed_fractions_and_percents():
    numbers = text_numbers("to 3-3/4 to 4 percent, and 2 percent goal; 0.5 percentage point")
    assert {3.75, 4.0, 2.0, 0.5} <= set(numbers)


def _cpi_event():
    return new_release_event(
        "cpi",
        period=date(2026, 8, 1),
        high_priority=True,
        facts={"indicator": "CPI", "yoy": 3.4, "prior_yoy": 3.3},
    )


def test_generate_brief_drops_bullets_citing_unknown_events(llm_for):
    event = _cpi_event()
    llm, _ = llm_for(
        {
            "BriefDraft": lambda p: {
                "bullets": [
                    {"text": "CPI rose to 3.4% from 3.3%.", "event_ids": [event.id]},
                    {"text": "Something else happened.", "event_ids": ["made_up:1"]},
                ]
            }
        }
    )
    result = generate_brief(
        llm,
        WhatChangedInput(as_of="2026-09-26", events=[event], context={}),
        indicator_source_urls={"cpi": "https://fred.stlouisfed.org/series/CPIAUCSL"},
        indicator_names={"cpi": "CPI"},
    )
    assert result.narrative_source == "llm"
    assert [b.text for b in result.bullets] == ["CPI rose to 3.4% from 3.3%."]
    assert result.bullets[0].citations[0].url == "https://fred.stlouisfed.org/series/CPIAUCSL"


def test_generate_brief_accepts_allowed_tenor_names(llm_for):
    event = _cpi_event()
    llm, _ = llm_for(
        {
            "BriefDraft": lambda p: {
                "bullets": [{"text": "CPI hit 3.4% as the 10Y held.", "event_ids": [event.id]}]
            }
        }
    )
    result = generate_brief(
        llm,
        WhatChangedInput(as_of="x", events=[event], context={}),
        indicator_source_urls={},
        indicator_names={},
    )
    assert result.narrative_source == "llm" and result.attempts == 1


def _fomc_block(changes):
    return FomcLatest(
        date=date(2026, 9, 16),
        url="https://www.federalreserve.gov/x",
        decision="hike",
        target_range=FomcTargetRange(lower=3.75, upper=4.0),
        change_bp=25,
        votes=FomcVotes(for_count=12),
        latest_text="The Committee decided to raise the target range. Inflation remains elevated.",
        changes=changes,
    )


def test_fomc_read_with_empty_diff_is_forced_unchanged(llm_for):
    llm, _ = llm_for(
        {
            "FomcReadDraft": lambda p: {
                "summary": "No change in wording.",
                "tone_shift": "more_hawkish",
                "rationale": "n/a",
                "cited_change_idx": [3],
                "key_phrases": [],
            }
        }
    )
    read = generate_fomc_read(llm, _fomc_block([])).read
    assert read.tone_shift == "unchanged" and read.cited_change_idx == []


def test_fomc_read_citing_nonexistent_change_falls_back(llm_for):
    changes = [FomcChange(idx=0, type="modified", before="maintain", after="raise the target range")]
    llm, _ = llm_for(
        {
            "FomcReadDraft": lambda p: {
                "summary": "Rates rose.",
                "tone_shift": "more_hawkish",
                "rationale": "See change 7.",
                "cited_change_idx": [99],
                "key_phrases": [],
            }
        }
    )
    read = generate_fomc_read(llm, _fomc_block(changes)).read
    assert read.narrative_source == "template"
    assert read.tone_shift == "more_hawkish" and read.cited_change_idx == [0]
