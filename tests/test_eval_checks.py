"""Tests for the pure eval checks (§11): style and event grounding."""

from __future__ import annotations

from evals.macro.event_grounding import check_event_grounding
from evals.macro.style_check import check_style


def test_style_ok_for_clean_bullet():
    result = check_style(["CPI rose 2.9% YoY in August, up from 2.7% in July."])
    assert result.ok is True
    assert result.violations == []


def test_style_flags_banned_word():
    result = check_style(["Inflation posted a shocking increase this month."])
    assert result.ok is False
    assert any(v.kind == "banned_word" for v in result.violations)


def test_style_flags_advice_language():
    result = check_style(["Investors should buy bonds given the rate cut."])
    assert result.ok is False
    assert any(v.kind == "advice_language" for v in result.violations)


def test_style_flags_bullet_over_30_words():
    long_bullet = " ".join(["word"] * 31)
    result = check_style([long_bullet])
    assert result.ok is False
    assert any(v.kind == "too_long" for v in result.violations)


def test_style_exactly_30_words_is_ok():
    bullet = " ".join(["word"] * 30)
    result = check_style([bullet])
    assert result.ok is True


def test_style_checks_every_bullet_independently():
    result = check_style(["This one is fine.", "This one is a shocking massive surge."])
    assert result.violations[0].bullet_index == 1


def test_style_preferred_verbs_do_not_trigger_advice_language():
    # "sell" as a substring of "unsold" or similar should not false-positive;
    # here we just confirm normal descriptive verbs pass cleanly.
    result = check_style(["Payrolls rose 142K while claims held steady."])
    assert result.ok is True


# -- event grounding --------------------------------------------------------------


def test_grounding_ok_when_all_event_ids_known_and_top_covered():
    bullets = [{"text": "CPI rose.", "event_ids": ["new_release:cpi:2026-08"]}]
    result = check_event_grounding(
        bullets, valid_event_ids={"new_release:cpi:2026-08"}, top_priority_event_id="new_release:cpi:2026-08"
    )
    assert result.ok is True
    assert result.unknown_event_ids == []
    assert result.top_priority_covered is True


def test_grounding_flags_unknown_event_id():
    bullets = [{"text": "CPI rose.", "event_ids": ["made_up_event"]}]
    result = check_event_grounding(
        bullets, valid_event_ids={"new_release:cpi:2026-08"}, top_priority_event_id=None
    )
    assert result.ok is False
    assert result.unknown_event_ids == ["made_up_event"]


def test_grounding_flags_uncovered_top_priority_event():
    bullets = [{"text": "Something else.", "event_ids": ["new_release:unrate:2026-08"]}]
    result = check_event_grounding(
        bullets,
        valid_event_ids={"new_release:unrate:2026-08", "fomc_decision:2026-09-16"},
        top_priority_event_id="fomc_decision:2026-09-16",
    )
    assert result.ok is False
    assert result.top_priority_covered is False


def test_grounding_none_top_priority_is_always_covered():
    result = check_event_grounding([], valid_event_ids=set(), top_priority_event_id=None)
    assert result.top_priority_covered is True
    assert result.ok is True
