"""§7 analyze.py: prompt building, fact collection, citation attachment,
tone-shift validation, and verbatim phrase filtering — all pure and
independent of agents_core. `generate_what_changed_brief` is exercised
against a stub `guarded_call` matching the shape `agents_core.llm.
call_with_number_guard` is expected to have.
"""

from __future__ import annotations

from datetime import date

from agents.macro.analyze import (
    WhatChangedInput,
    attach_citations,
    build_fomc_read_prompt,
    build_what_changed_prompt,
    collect_facts,
    filter_verbatim_key_phrases,
    generate_what_changed_brief,
    validate_tone_shift,
)
from agents.macro.events import new_release_event
from agents.macro.fomc import Change


def test_build_what_changed_prompt_shape():
    events = [new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9})]
    payload = WhatChangedInput(as_of="2026-09-11", events=events, context={"regimes": {}})
    prompt = build_what_changed_prompt(payload)
    assert prompt["as_of"] == "2026-09-11"
    assert prompt["events"][0]["id"] == events[0].id
    assert prompt["events"][0]["facts"] == {"yoy": 2.9}
    assert prompt["context"] == {"regimes": {}}


def test_collect_facts_flat_and_nested():
    events = [
        new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9, "mom": 0.3}),
        new_release_event(
            "unrate",
            period=date(2026, 8, 1),
            high_priority=True,
            facts={"range": {"lower": 4.0, "upper": 4.25}},
        ),
    ]
    facts = collect_facts(events)
    assert set(facts) == {2.9, 0.3, 4.0, 4.25}


def test_collect_facts_ignores_non_numeric_and_bools():
    events = [
        new_release_event(
            "fomc", period=date(2026, 9, 16), high_priority=True, facts={"decision": "hold", "delayed": True}
        ),
    ]
    assert collect_facts(events) == []


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


# -- generate_what_changed_brief against a stub guarded_call ---------------------


class _StubResult:
    def __init__(self, text, narrative_source):
        self.text = text
        self.narrative_source = narrative_source


def test_generate_what_changed_brief_passes_through_llm_result():
    events = [new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9})]
    payload = WhatChangedInput(as_of="2026-09-11", events=events, context={})

    def stub_guarded_call(*, run_id, agent, prompt, facts, call, template_fallback, allow=()):
        assert run_id == "run-1"
        assert agent == "macro"
        assert facts == [2.9]
        return _StubResult(text='{"bullets": []}', narrative_source="llm")

    text, source = generate_what_changed_brief(
        run_id="run-1",
        payload=payload,
        event_by_id={e.id: e for e in events},
        indicator_source_urls={"cpi": "https://fred.stlouisfed.org/series/CPIAUCSL"},
        guarded_call=stub_guarded_call,
        call_llm=lambda prompt: "unused",
        template_fallback=lambda: "unused",
    )
    assert source == "llm"
    assert text == '{"bullets": []}'


def test_generate_what_changed_brief_reports_template_fallback():
    events = [new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9})]
    payload = WhatChangedInput(as_of="2026-09-11", events=events, context={})

    def stub_guarded_call(*, run_id, agent, prompt, facts, call, template_fallback, allow=()):
        return _StubResult(text="CPI rose 2.9% YoY. (template)", narrative_source="template")

    text, source = generate_what_changed_brief(
        run_id="run-1",
        payload=payload,
        event_by_id={e.id: e for e in events},
        indicator_source_urls={},
        guarded_call=stub_guarded_call,
        call_llm=lambda prompt: "unused",
        template_fallback=lambda: "unused",
    )
    assert source == "template"
