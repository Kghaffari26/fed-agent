"""Export agents/macro/schema.py's MacroOutput model to schemas/macro.schema.json.

This is the committed snapshot tests/test_schema.py checks against. The agents-core
runner also publishes the same schema as `public-data/schema.json` on every run
(via `agents_core.export_schemas.write_schema`), so this file exists for review
diffs and site codegen, not for the data branch.

Usage:
    uv run python scripts/export_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agents_core.export_schemas import schema_dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.macro.schema import MacroOutput  # noqa: E402

OUT_PATH = Path("schemas/macro.schema.json")


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(schema_dict(MacroOutput), indent=2) + "\n")
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
