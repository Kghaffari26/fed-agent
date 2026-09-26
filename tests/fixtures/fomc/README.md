# Federal Reserve fixtures — real pages, saved 2026-09-26

Saved verbatim from `www.federalreserve.gov` (the previous hand-reconstructed pages are gone). Tests parse
them offline; nothing here is fetched during `pytest`.

| File | Source | Used for |
|---|---|---|
| `statement_2022_06_15.html` | `newsevents/pressreleases/monetary20220615a.htm` | 75 bp hike, named "Voting for" list, one dissent (George) whose preference contains a decimal; non-breaking hyphens in "1‑1/2" |
| `statement_2024_07_31.html` | `monetary20240731a.htm` | Hold at 5-1/4 to 5-1/2, unanimous 12 named votes; the "before" side of the 2024 cut diff |
| `statement_2024_09_18.html` | `monetary20240918a.htm` | 50 bp cut ("by 1/2 percentage point"), one dissent (Bowman) |
| `statement_2026_06_17.html` | `monetary20260617a.htm` | 2026 page layout: "approved ... by a 12 – 0 vote:" preface instead of a named list; near-identical to July |
| `statement_2026_07_29.html` | `monetary20260729a.htm` | Hold, "9 – 3 vote", three named dissenters preferring a hike |
| `statement_2026_09_16.html` | `monetary20260916a.htm` | 25 bp hike with `<strong> </strong>` inside the decision sentence (the extractor must not glue words) |
| `minutes_2026_07_29.html` | `monetarypolicy/fomcminutes20260729.htm` | Minutes text extraction (footnotes dropped) |
| `fomccalendars.html` | `monetarypolicy/fomccalendars.htm` | Meeting-calendar parse; checked against `config/fomc_dates.toml` |
| `press_monetary.xml` | `feeds/press_monetary.xml` | RSS discovery of statements and FOMC minutes (15 items) |

Three different years (2022, 2024, 2026) and two page generations, per SPEC_MACRO.md §3/§11. If the Fed
changes its layout again, save a fresh page here and add it to the parametrized tests in
`tests/test_fomc.py`.
