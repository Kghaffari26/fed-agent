"""§5.3 revision detection: the classic payroll-revision case, epsilon
tolerance, and the "only the last 3 periods" cap.
"""

from __future__ import annotations

from datetime import date

from agents.macro.fetch_fred import Observation
from agents.macro.revisions import detect_revisions, epsilon_for


def test_epsilon_for_thousands_is_1():
    assert epsilon_for("thousands") == 1.0


def test_epsilon_for_percent_default_is_0_05():
    assert epsilon_for(None) == 0.05
    assert epsilon_for("percent") == 0.05


def test_classic_payroll_revision_jul_73k_to_41k():
    stored = {"2026-07-01": 73.0}
    new = [Observation(date=date(2026, 7, 1), value=41.0)]
    revisions = detect_revisions(stored, new, units_scale="thousands")
    assert len(revisions) == 1
    assert revisions[0].period == date(2026, 7, 1)
    assert revisions[0].old == 73.0
    assert revisions[0].new == 41.0


def test_no_revision_when_values_match_exactly():
    stored = {"2026-07-01": 41.0}
    new = [Observation(date=date(2026, 7, 1), value=41.0)]
    assert detect_revisions(stored, new, units_scale="thousands") == []


def test_float_noise_below_epsilon_is_ignored_for_counts():
    stored = {"2026-07-01": 41.0}
    new = [Observation(date=date(2026, 7, 1), value=41.4)]  # |diff|=0.4 < epsilon 1.0
    assert detect_revisions(stored, new, units_scale="thousands") == []


def test_diff_exactly_at_epsilon_is_not_a_revision_strict_greater_than():
    stored = {"2026-07-01": 41.0}
    new = [Observation(date=date(2026, 7, 1), value=42.0)]  # |diff|=1.0 == epsilon
    assert detect_revisions(stored, new, units_scale="thousands") == []


def test_diff_just_above_epsilon_is_a_revision():
    stored = {"2026-07-01": 41.0}
    new = [Observation(date=date(2026, 7, 1), value=42.01)]
    revisions = detect_revisions(stored, new, units_scale="thousands")
    assert len(revisions) == 1


def test_percent_epsilon_boundary():
    stored = {"2026-08-01": 2.90}
    below = detect_revisions(stored, [Observation(date=date(2026, 8, 1), value=2.94)])
    at = detect_revisions(stored, [Observation(date=date(2026, 8, 1), value=2.95)])
    above = detect_revisions(stored, [Observation(date=date(2026, 8, 1), value=2.96)])
    assert below == []
    assert at == []  # |diff| == 0.05, not > 0.05
    assert len(above) == 1


def test_missing_new_value_is_never_a_revision():
    stored = {"2026-08-01": 2.9}
    new = [Observation(date=date(2026, 8, 1), value=None)]
    assert detect_revisions(stored, new) == []


def test_date_with_no_stored_value_is_never_a_revision():
    new = [Observation(date=date(2026, 8, 1), value=2.9)]
    assert detect_revisions({}, new) == []


def test_only_last_3_periods_surfaced():
    stored = {
        "2026-04-01": 100.0,
        "2026-05-01": 100.0,
        "2026-06-01": 100.0,
        "2026-07-01": 100.0,
    }
    new = [
        Observation(date=date(2026, 4, 1), value=110.0),
        Observation(date=date(2026, 5, 1), value=110.0),
        Observation(date=date(2026, 6, 1), value=110.0),
        Observation(date=date(2026, 7, 1), value=110.0),
    ]
    revisions = detect_revisions(stored, new, units_scale="thousands")
    assert len(revisions) == 3
    assert [r.period for r in revisions] == [date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)]
