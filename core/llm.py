"""Shared LLM call wrapper implementing the number-guard retry policy.

See docs/specs/SPEC_MACRO.md §7.4. Every agent that asks a model to write
prose about computed facts routes the call through `call_with_number_guard`
so an ungrounded number never reaches the site: the guard checks the first
attempt, a single retry asks the model to fix unsupported numbers, and a
second failure falls back to deterministic template text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

from core.guards import verify_numbers

GUARD_FAILURE_LOG = Path("data/guard_failures.jsonl")


class LLMCall(Protocol):
    def __call__(self, prompt: str) -> str: ...


@dataclass
class GuardedLLMResult:
    text: str
    narrative_source: str  # "llm" | "template"
    guard_ok: bool
    unsupported: list[str]


def call_with_number_guard(
    *,
    run_id: str,
    agent: str,
    prompt: str,
    facts: Iterable[float],
    call: LLMCall,
    template_fallback: Callable[[], str],
    allow: Iterable[str] = (),
    log_path: Path = GUARD_FAILURE_LOG,
) -> GuardedLLMResult:
    """Call `call(prompt)` and verify its output against `facts`.

    On a guard failure, retries once with the unsupported tokens named in the
    prompt. On a second failure, logs the failure and returns the
    deterministic `template_fallback()` text with `narrative_source="template"`.
    """
    facts = list(facts)

    output = call(prompt)
    result = verify_numbers(output, facts, allow=allow)
    if result.ok:
        return GuardedLLMResult(text=output, narrative_source="llm", guard_ok=True, unsupported=[])

    retry_prompt = (
        f"{prompt}\n\n"
        f"These numbers are not in the input: {result.unsupported}. "
        "Rewrite using only provided numbers."
    )
    output = call(retry_prompt)
    result = verify_numbers(output, facts, allow=allow)
    if result.ok:
        return GuardedLLMResult(text=output, narrative_source="llm", guard_ok=True, unsupported=[])

    _log_guard_failure(
        log_path=log_path,
        run_id=run_id,
        agent=agent,
        prompt=retry_prompt,
        output=output,
        unsupported=result.unsupported,
    )
    return GuardedLLMResult(
        text=template_fallback(),
        narrative_source="template",
        guard_ok=False,
        unsupported=result.unsupported,
    )


def _log_guard_failure(
    *, log_path: Path, run_id: str, agent: str, prompt: str, output: str, unsupported: list[str]
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": run_id,
        "agent": agent,
        "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
        "output": output,
        "unsupported": unsupported,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    with log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
