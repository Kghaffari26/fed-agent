"""Export agents/macro/schema.py's MacroOutput model to schemas/macro.schema.json.

Usage:
    uv run python scripts/export_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.macro.schema import MacroOutput  # noqa: E402

OUT_PATH = Path("schemas/macro.schema.json")


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema = MacroOutput.model_json_schema()
    OUT_PATH.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
