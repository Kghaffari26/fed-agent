# FOMC statement fixtures — RECONSTRUCTED, not live captures

This environment's network policy blocks `www.federalreserve.gov` (confirmed: a direct request gets a
`403` at the egress proxy — see `STATUS.md`), so these three statement pages could not be fetched live as
the spec asks. They are **hand-written to faithfully match the real page structure and the Fed's real
statement language patterns** (the `div#article` container, the release-time header, the policy paragraphs,
the voting paragraph, the "Implementation Note" and "For media inquiries" boilerplate the extractor must
drop) — but they are reconstructions, not captures, and the exact wording of any real 2026 statement is
guesswork.

**Once network access is broadened, replace these three files** with real pages fetched from
`https://www.federalreserve.gov/newsevents/pressreleases/monetaryYYYYMMDDa.htm` (three different years, per
SPEC_MACRO.md §11's `test_fomc_parse.py` requirement), and re-run `uv run pytest tests/test_fomc.py`. The
parsing/diff logic itself doesn't need to change unless a real page's structure differs from what's
modeled here.

| File | Models |
|---|---|
| `statement_2026_01_28.html` | A hold decision, target range 4.25–4.50%, unanimous vote. |
| `statement_2026_03_18.html` | A hold decision, same range, near-identical language to January (one small wording change) — for testing an "unchanged"/minor tone diff. |
| `statement_2026_09_16.html` | A 25bp cut to 4.00–4.25%, with one dissent preferring to hold, and substantially reworded economic-outlook language — for testing decision/vote parsing and a larger diff. |
