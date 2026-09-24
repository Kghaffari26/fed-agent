"""The "Event grounding" eval (§11): every bullet's event_ids exist in the
input, and the top-priority event is covered by some bullet.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GroundingResult:
    ok: bool
    unknown_event_ids: list[str] = field(default_factory=list)
    top_priority_covered: bool = True


def check_event_grounding(
    bullets: list[dict], *, valid_event_ids: set[str], top_priority_event_id: str | None
) -> GroundingResult:
    unknown: list[str] = []
    covered_ids: set[str] = set()
    for bullet in bullets:
        for event_id in bullet.get("event_ids", []):
            covered_ids.add(event_id)
            if event_id not in valid_event_ids:
                unknown.append(event_id)

    top_covered = top_priority_event_id is None or top_priority_event_id in covered_ids
    return GroundingResult(
        ok=not unknown and top_covered,
        unknown_event_ids=unknown,
        top_priority_covered=top_covered,
    )
