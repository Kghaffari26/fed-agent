"""Deterministic template fallback text — must never invent a number, so
every value comes straight from the event's own `facts`."""

from __future__ import annotations

from datetime import date

from agents.macro.events import (
    delayed_event,
    fomc_decision_event,
    new_release_event,
    regime_change_event,
    revision_event,
    threshold_cross_events,
)
from agents.macro.revisions import Revision
from agents.macro.templates import headline_for_event, template_brief


def test_headline_fomc_hold():
    e = fomc_decision_event(
        statement_date=date(2026, 9, 16), decision="hold", target_range={"lower": 4.0, "upper": 4.25}
    )
    headline = headline_for_event(e)
    assert "held rates steady" in headline
    assert "4.0-4.25%" in headline.replace("–", "-")


def test_headline_fomc_cut():
    e = fomc_decision_event(
        statement_date=date(2026, 9, 16), decision="cut", target_range={"lower": 4.0, "upper": 4.25}
    )
    assert "cut rates" in headline_for_event(e)


def test_headline_new_release_with_yoy_and_prior():
    e = new_release_event(
        "cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9, "prior_yoy": 2.7}
    )
    headline = headline_for_event(e, indicator_name="CPI")
    assert "2.9%" in headline
    assert "2.7%" in headline
    assert "CPI" in headline


def test_headline_new_release_payrolls_mom_diff():
    e = new_release_event("payrolls", period=date(2026, 8, 1), high_priority=True, facts={"mom_diff": 142.0})
    headline = headline_for_event(e, indicator_name="Nonfarm payrolls")
    assert "+142K" in headline


def test_headline_revision():
    r = Revision(period=date(2026, 7, 1), old=73.0, new=41.0)
    e = revision_event("payrolls", r, is_payrolls=True)
    headline = headline_for_event(e, indicator_name="Payrolls")
    assert "73.0" in headline
    assert "41.0" in headline


def test_headline_threshold_cross_up():
    events = threshold_cross_events(
        "cpi", prior_value=2.8, new_value=3.1, thresholds=[3.0], period=date(2026, 8, 1)
    )
    headline = headline_for_event(events[0], indicator_name="CPI")
    assert "above" in headline
    assert "3.0" in headline


def test_headline_delayed():
    e = delayed_event("umich_sentiment", scheduled_release=date(2026, 9, 1))
    headline = headline_for_event(e, indicator_name="UMich sentiment")
    assert "delayed" in headline.lower()


def test_headline_regime_change():
    e = regime_change_event("labor", old_label="Stable", new_label="Softening", period=date(2026, 9, 1))
    headline = headline_for_event(e)
    assert "Stable" in headline
    assert "Softening" in headline


def test_template_brief_only_uses_facts_numbers():
    events = [
        new_release_event(
            "cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9, "prior_yoy": 2.7}
        ),
    ]
    bullets = template_brief(events, indicator_names={"cpi": "CPI"})
    assert len(bullets) == 1
    assert "2.9%" in bullets[0]


def test_template_brief_preserves_event_order():
    events = [
        new_release_event("cpi", period=date(2026, 8, 1), high_priority=True, facts={"yoy": 2.9}),
        new_release_event("unrate", period=date(2026, 8, 1), high_priority=True, facts={"level": 4.4}),
    ]
    bullets = template_brief(events, indicator_names={"cpi": "CPI", "unrate": "Unemployment"})
    assert "CPI" in bullets[0]
    assert "Unemployment" in bullets[1]
