"""Tests for core.guards.verify_numbers (docs/specs/SPEC_MACRO.md §7.4)."""

from __future__ import annotations

import json

import pytest

from core.guards import GuardResult, verify_numbers
from core.llm import call_with_number_guard

# Each case: (text, facts, expect_ok, [expected unsupported substrings, optional])
CASES = [
    # -- basic matches --
    ("CPI rose 2.9% YoY.", [2.9], True),
    ("Core PCE cooled to 2.4% on a 3-month annualized basis.", [2.4], True),
    ("Unemployment holds at 4%.", [4.0], True),
    ("core PCE cooled to 2.4% while headline sits at 2.9%.", [2.9, 2.4], True),
    # -- basic mismatches --
    ("CPI rose 3.5% YoY.", [2.9], False),
    ("CPI rose 2.9% while core CPI held at 5.0%.", [2.9], False),
    ("GDP grew 9.9% annualized.", [], False),
    # -- sign handling (abs on both sides) --
    ("Unemployment fell 0.3 pp.", [-0.3], True),
    ("GDP contracted -0.5%.", [-0.5], True),
    ("The rate fell -0.3, matching expectations.", [0.3], True),
    # -- thousands / K, M, B scale suffixes --
    ("Payrolls rose 142K.", [142000], True),
    ("Payrolls rose 142K.", [141000], False),
    ("Payrolls rose +142K in August.", [142000], True),
    ("The fund grew to $1.2M.", [1_200_000], True),
    ("The fund grew to $1.2M.", [1_100_000], False),
    ("Assets under management reached $2.5B.", [2_500_000_000], True),
    # -- comma-grouped counts and dollar amounts --
    ("Payrolls added 1,234 jobs.", [1234], True),
    ("The estimate was $1,234.5 million.", [1234.5], True),
    # -- basis points / percentage points units --
    ("The Fed cut by 25 bp.", [25], True),
    ("The Fed cut by 25 bp.", [50], False),
    ("The spread widened by 0.25 pp.", [0.25], True),
    ("The Fed cut by 25 bp, or 0.25 pp.", [25, 0.25], True),
    # -- fractional target ranges ("4-1/4" style) --
    ("The Committee raised the range to 4-1/4 percent.", [4.25], True),
    ("The Committee held the range at 4-3/4 percent.", [4.75], True),
    ("The Committee raised the range to 4-1/4 percent.", [4.5], False),
    # -- ignored: years --
    ("In 2026, CPI rose 2.9% YoY.", [2.9], True),
    ("Between 2024 and 2026, the curve normalized.", [], True),
    ("The 2026 reading of 2.9% matched expectations.", [2.9], True),
    # -- ignored: day-of-month after a month name --
    ("On Sep 19 CPI rose 2.9% YoY.", [2.9], True),
    ("On September 19 inflation eased to 2.9%.", [2.9], True),
    ("The statement was released Jan 28 with a hold decision.", [], True),
    # -- ignored: known terms (N-year, N-month, Q1-Q4, 401(k)) --
    ("The 2-year yield fell 0.1 pp.", [-0.1], True),
    ("The 10-year yield rose 0.06 pp to 4.12%.", [0.06, 4.12], True),
    ("The 30-year mortgage rate held near 6.5%.", [6.5], True),
    ("Core CPI's 3-month annualized pace slowed to 2.4%.", [2.4], True),
    ("Q1 GDP grew 2.1% annualized.", [2.1], True),
    ("Q4 growth matched Q1 at 2.1%.", [2.1], True),
    ("401(k) contribution limits were unchanged this year.", [], True),
    # -- ignored: ordinals --
    ("This is the 21st consecutive month of gains.", [], True),
    ("The 3rd release this quarter showed 2.9% CPI.", [2.9], True),
    # -- ignored: caller-provided allow list --
    ("The target stays near 5, unchanged from last meeting.", [], False),
    ("The target stays near 5, unchanged from last meeting.", [], True, ["5"]),
    # -- rounding tolerance --
    ("Core PCE held at 2.9%.", [2.9000000001], True),
    ("Core PCE held at 2.9%.", [2.7], False),
    # -- no numbers at all --
    ("The Committee held rates steady.", [], True),
    ("Job gains have remained solid in recent months.", [], True),
    # -- multiple facts, only one relevant --
    ("Headline CPI rose to 2.9% YoY in August from 2.7%.", [2.7, 2.9], True),
    # -- decimals differ from facts' natural precision --
    ("The 10-year yield sits at 4.12%, up 0.06 pp.", [4.12, 0.06], True),
    ("The 10-year yield sits at 4.1%, up 0.06 pp.", [4.12, 0.06], True),  # 4.1 rounds to 4.1 either way
]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_verify_numbers_cases(case):
    if len(case) == 4:
        text, facts, expect_ok, allow = case
    else:
        text, facts, expect_ok = case
        allow = ()
    result = verify_numbers(text, facts, allow=allow)
    assert result.ok is expect_ok, f"unsupported={result.unsupported!r}"


def test_guard_result_is_dataclass_with_expected_fields():
    result = verify_numbers("CPI rose 2.9%.", [2.9])
    assert isinstance(result, GuardResult)
    assert result.ok is True
    assert result.unsupported == []


def test_none_facts_are_ignored_without_crashing():
    result = verify_numbers("CPI rose 2.9%.", [None, 2.9])
    assert result.ok is True


def test_unsupported_lists_the_offending_token_text():
    result = verify_numbers("Core CPI held at 5.0%.", [2.9])
    assert result.ok is False
    assert result.unsupported == ["5.0%"]


def test_multiple_unsupported_tokens_all_reported():
    result = verify_numbers("CPI rose 3.5% and core CPI rose 6.0%.", [2.9])
    assert result.ok is False
    assert set(result.unsupported) == {"3.5%", "6.0%"}


# ---------------------------------------------------------------------------
# core.llm.call_with_number_guard: retry-once-then-template-fallback policy
# ---------------------------------------------------------------------------


def test_guarded_call_passes_through_when_first_attempt_is_grounded():
    calls = []

    def call(prompt: str) -> str:
        calls.append(prompt)
        return "CPI rose 2.9% YoY."

    result = call_with_number_guard(
        run_id="run-1",
        agent="macro",
        prompt="write a bullet",
        facts=[2.9],
        call=call,
        template_fallback=lambda: "template text",
    )
    assert result.guard_ok is True
    assert result.narrative_source == "llm"
    assert result.text == "CPI rose 2.9% YoY."
    assert len(calls) == 1


def test_guarded_call_retries_once_and_succeeds():
    outputs = iter(["CPI rose 9.9% YoY.", "CPI rose 2.9% YoY."])
    calls = []

    def call(prompt: str) -> str:
        calls.append(prompt)
        return next(outputs)

    result = call_with_number_guard(
        run_id="run-2",
        agent="macro",
        prompt="write a bullet",
        facts=[2.9],
        call=call,
        template_fallback=lambda: "template text",
    )
    assert result.guard_ok is True
    assert result.narrative_source == "llm"
    assert len(calls) == 2
    assert "9.9%" in calls[1]  # retry prompt names the unsupported token


def test_guarded_call_falls_back_to_template_after_second_failure(tmp_path):
    log_path = tmp_path / "guard_failures.jsonl"

    def call(prompt: str) -> str:
        return "CPI rose 9.9% YoY."

    result = call_with_number_guard(
        run_id="run-3",
        agent="macro",
        prompt="write a bullet",
        facts=[2.9],
        call=call,
        template_fallback=lambda: "CPI rose 2.9% YoY. (template)",
        log_path=log_path,
    )
    assert result.guard_ok is False
    assert result.narrative_source == "template"
    assert result.text == "CPI rose 2.9% YoY. (template)"

    assert log_path.exists()
    entry = json.loads(log_path.read_text().strip().splitlines()[-1])
    assert entry["run_id"] == "run-3"
    assert entry["agent"] == "macro"
    assert "9.9%" in entry["unsupported"]
