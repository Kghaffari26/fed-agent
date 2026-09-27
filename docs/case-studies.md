# Case studies

Five real incidents from this repo's history. Each one is written as problem → how it was caught → fix →
result. Sources are [`DECISIONS.md`](../DECISIONS.md), [`STATUS.md`](../STATUS.md), the eval results in
`evals/results/`, and the commits linked below.

---

## 1. GDP was flagged "delayed" when it wasn't

**Problem.** After the first real run on 2026-09-26, the GDP indicator showed `delayed: true`. FRED's
`A191RL1Q225SBEA` was last updated on 2026-07-30, and FRED's calendar for the GDP release (release 53) listed
2026-08-26. By the original §5.5 rule ("a scheduled date passed and the series didn't update"), that counted as
a delay. The site would have shown a "delayed" badge on a release that had in fact come out.

**How it was caught.** In the live run. The flag was implausible (no shutdown, no BEA notice), so it went into
STATUS.md as a hand-check item instead of being trusted.

**Fix.** [`bdc81dd`](https://github.com/Kghaffari26/fed-agent/commit/bdc81dd). The rule now uses FRED's
actual release status. FRED's `release/dates` without `include_release_dates_with_no_data` returns only the
dates it really published data for. On 2026-09-27 that list contained 2026-08-26, which means the release came
out and just didn't revise this series. A release now counts as delayed only when all of these hold:

- its last scheduled date is missing from the published list;
- that date is more than 2 days old;
- the series hasn't updated since.

A date that is scheduled but not yet due (2026-09-30) is never delayed. If the published-dates lookup fails,
nothing is flagged, because a false "delayed" is worse than a late one.

**Result.** The live dry run on 2026-09-27 shows GDP as not delayed, with `next=2026-09-30`. Four tests pin
the behavior:

- the real GDP case;
- a shutdown-style miss;
- scheduled but not yet due;
- unknown status.

---

## 2. The real 2026 FOMC statement broke the parser

**Problem.** The FOMC parser was first built against hand-reconstructed statements, because
federalreserve.gov was unreachable from the build sandbox. The real September 2026 page differed in four
ways:

- a "by a 9 – 3 vote" preface that names only the dissenters;
- "raise … by 1/4 percentage point to";
- words glued together by `text(strip=True)`;
- non-breaking hyphens.

Decision, vote and target-range parsing all failed on it.

**How it was caught.** By tests. Once network access allowed it, six real statement pages (from 2022, 2024
and 2026), the real minutes, the calendar page and the RSS feed replaced the reconstructed fixtures. The
existing parser tests then failed on the real text.

**Fix.** [`d96f298`](https://github.com/Kghaffari26/fed-agent/commit/d96f298). The parser now handles:

- the vote-tally preface;
- the "by N percentage point to" form;
- bare-fraction ranges ("3/4 to 1 percent");
- whitespace-preserving text extraction;
- hyphen normalization.

**Result.** Every fixture is a real page. The end-to-end test parses the 2026-09-16 hike as `hike`, `+25`
bp, 3.75–4.00%. The real runs on 2026-09-26 and 2026-09-27 published the correct decision.

---

## 3. A contradictory "+0.0" change next to two different numbers

**Problem.** In the first real run, an indicator showed 3.4 against a prior 3.3, with a change of `0.0`. The
raw values were 3.36 and 3.31. The code rounded their raw difference (0.05) to one decimal, which came out as
0.0. That is arithmetically true and visibly wrong on the page.

**How it was caught.** By reading the output of the live run, not by a test. The unit tests checked the raw
delta, which was "right".

**Fix.** [`d96f298`](https://github.com/Kghaffari26/fed-agent/commit/d96f298). `display.displayed_delta`
computes a change from the values as displayed: 3.4 − 3.3 = +0.1. Rounding moved entirely to publish time
(§5.1). Event facts are rounded with the same rule, so the model and the number guard see exactly the numbers
the site shows.

**Result.** Deltas always agree with the two numbers next to them. Because event facts use the same
rounding, the model can only quote what the site shows, and the guard checks against the same values.

---

## 4. The model "helpfully" converted 1/4 point into "25 basis point"

**Problem.** In the FOMC-read eval, the model summarized a "1/4 percentage point" hike as "25 basis point".
The conversion is correct, but it is a number the model computed rather than one it was given. That is the
exact thing §7 forbids, because the next conversion might be wrong.

**How it was caught.** By the number guard. `25` wasn't among the facts, so the guard rejected the first
attempt and retried with the bad token named. The retry used the Fed's own wording and passed. It is logged in
`evals/results/guard_failures.jsonl` on 2026-09-26, and the same pair was caught and recovered again in the
2026-09-27 eval run.

**Fix.** No code change was needed; this is the guard working as designed (§7.4).
[`d96f298`](https://github.com/Kghaffari26/fed-agent/commit/d96f298) added the one related change: the FOMC
read's guard facts include numbers already printed in the Fed text ("2 percent", "3-3/4"), so quoting the
statement never trips it.

**Result.**

- Phrase-verbatim and number fidelity are 100% in both eval runs.
- The FOMC-read suite's first-attempt misses are visible in history instead of silently published.
- The same guard now checks the release investigator's final analysis against every tool output.

---

## 5. The release investigator speculated about the Fed's motives

**Problem.** The first eval run of the new release investigator (the agent loop, §6.1) passed every
trajectory check:

- required tools called;
- no invented tools;
- at most 6 steps;
- `finished`;
- every number supported.

But the LLM judge scored it 0.65. For the FOMC day, the analysis said the cut was "suggesting the Fed is
prioritizing labor market conditions" and "likely motivated easing". Those are claims about intent that no
tool supports. The CPI analysis also ran well past the length target.

**How it was caught.** By an eval: the LLM judge in `evals/macro/suites.py` (`judge_quality`). Reading the
judge's reasoning also turned up an eval bug. The judge was grading against the scenario fixture's
hypothetical numbers (payrolls +22K), while the loop had correctly used the real recorded FRED data (+162K). It
called that a "critical factual error".

**Fix.** [`fc0e169`](https://github.com/Kghaffari26/fed-agent/commit/fc0e169). Changes to the investigator:

- a new prompt rule: "Describe, don't speculate: never attribute motives to the Fed";
- a 90-word target, with the 120-word hard cap still enforced in code;
- `PROMPT_VERSION` bumped.

Changes to the eval:

- the case input is now exactly what the loop sees: the trigger with its facts recomputed from the recorded
  data, plus the FOMC context;
- the rubric says tool-derived numbers are verified separately.

**Result.** `judge_quality` went from 0.65 to **1.00** and the pass rate from 0.60 to **1.00**, for $0.04 per
run. Both entries are in `evals/history.jsonl`, so `agents-evals compare` shows the change. The live run on
2026-09-27 produced a neutral FOMC analysis ("…indicating a tighter labor market alongside firmer
inflation"), with its three tool calls and $0.0087 cost visible in `trace.json`.
