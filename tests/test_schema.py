"""§6 output schema: the example fixture (matching SPEC_MACRO.md §6) validates,
and the exported JSON Schema is stable."""

from __future__ import annotations

import json
from pathlib import Path

from agents.macro.schema import MacroOutput

FIXTURE = Path(__file__).parent / "fixtures" / "schema" / "latest_example.json"
EXPORTED_SCHEMA = Path("schemas/macro.schema.json")


def test_spec_example_fixture_validates():
    raw = json.loads(FIXTURE.read_text())
    output = MacroOutput.model_validate(raw)
    assert output.headline.startswith("August CPI rose")
    assert output.meta.data_changed is True
    assert output.regimes.inflation.label == "Cooling"


def test_fixture_size_under_350kb():
    assert FIXTURE.stat().st_size < 350_000


def test_round_trips_through_json():
    raw = json.loads(FIXTURE.read_text())
    output = MacroOutput.model_validate(raw)
    dumped = json.loads(output.model_dump_json())
    reparsed = MacroOutput.model_validate(dumped)
    assert reparsed.headline == output.headline


def test_null_optional_fields_stay_null_not_zero():
    raw = json.loads(FIXTURE.read_text())
    output = MacroOutput.model_validate(raw)
    cpi = next(i for i in output.indicators if i.id == "cpi")
    assert cpi.revision is None
    assert output.brief.reused_from_run_id is None


def test_revision_block_present_for_payrolls():
    raw = json.loads(FIXTURE.read_text())
    output = MacroOutput.model_validate(raw)
    payrolls = next(i for i in output.indicators if i.id == "payrolls")
    assert payrolls.revision is not None
    assert payrolls.revision.old == 73
    assert payrolls.revision.new == 41


def test_fomc_tone_shift_populated_with_cited_change_idx():
    raw = json.loads(FIXTURE.read_text())
    output = MacroOutput.model_validate(raw)
    read = output.fomc.latest.read
    assert read.tone_shift == "more_dovish"
    assert read.cited_change_idx == [0]


def test_exported_json_schema_matches_snapshot():
    exported = json.loads(EXPORTED_SCHEMA.read_text())
    current = MacroOutput.model_json_schema()
    assert exported == current, (
        "schemas/macro.schema.json is stale — run `uv run python scripts/export_schema.py`"
    )
