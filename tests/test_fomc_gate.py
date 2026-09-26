"""scripts/fomc_gate.py: the FOMC-afternoon cron only runs on decision days (§9)."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

from scripts.fomc_gate import FOMC_AFTERNOON_CRON, decision_days, should_run

GATE = Path(__file__).resolve().parent.parent / "scripts" / "fomc_gate.py"


def test_afternoon_cron_runs_on_decision_day():
    assert should_run(FOMC_AFTERNOON_CRON, date(2026, 10, 28)) is True


def test_afternoon_cron_skips_first_day_of_meeting_and_ordinary_days():
    assert should_run(FOMC_AFTERNOON_CRON, date(2026, 10, 27)) is False
    assert should_run(FOMC_AFTERNOON_CRON, date(2026, 9, 25)) is False


def test_morning_cron_and_manual_runs_always_run():
    assert should_run("0 14 * * 1-5", date(2026, 9, 25)) is True
    assert should_run("", date(2026, 9, 25)) is True  # workflow_dispatch has no schedule


def test_decision_days_match_config():
    days = decision_days()
    assert date(2026, 9, 16) in days and date(2026, 12, 9) in days


def test_cli_prints_github_output_line():
    out = subprocess.run(
        [sys.executable, str(GATE), "2026-09-25"],
        env={"SCHEDULE": FOMC_AFTERNOON_CRON},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "run=false"
