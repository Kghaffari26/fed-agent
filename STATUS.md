# Status

**2026-09-26.** Every task that was blocked on `agents-core` or on network access is done. agents-core
`v0.1.0` is installed and wired in, the agent runs through `uv run agents-run macro [--dry-run]`, a real
run published the data-branch contract, and an immediate re-run made zero LLM calls. See `DECISIONS.md` for
each judgment call made along the way.

## Done this session

- **agents-core `v0.1.0`** pinned (`uv add "agents-core @ git+https://github.com/Kghaffari26/agents-core@v0.1.0"`,
  resolves to `b0a292d`). All the local stand-ins are gone:
  - HTTP: `fetch_fred.py` and `fetch_fed.py` take `agents_core.http.Http`, with per-host rate limits of
    2 req/s for FRED and 1 req/s for the Fed.
  - LLM, guard and costs: `analyze.py` calls `ctx.llm.structured(..., guard=fields_guard(...), fallback=...)`.
  - Output schema: `MacroOutput` is an `agents_core.schema.AgentOutput`, so `meta` is agents-core's `RunMeta`.
  - Publish and runner come from agents-core. The schema export uses `agents_core.export_schemas`.
- **Registration**: `[project.entry-points."agents_core.agents"] macro = "agents.macro.agent:AGENT"`.
  `uv run agents-run --list` shows it, and `uv run agents-run macro --dry-run` fetches all 25 series,
  prints indicators, regimes and events, and makes no LLM call.
- **LLM calls**: the what-changed brief, FOMC read and minutes summary all go through `agents_core.llm`
  with the number guard. A guard failure retries once and then falls back to `templates.py`
  (`narrative_source: "template"`). A refusal or truncation also falls back. `BudgetExceeded` fails the run
  (§10). Citations are attached by code.
- **Publishing** follows the agents-core data-branch contract. `public-data/` holds `latest.json`,
  `history/YYYY-MM-DD.json`, `manifest-entry.json`, `costs-summary.json` and `schema.json`, with the §6
  shapes unchanged (the `meta` block is agents-core's shared one). Run state goes in `data/`: `macro/state.json`,
  `costs.jsonl` and `guard_failures.jsonl`.
- **Workflow**: `.github/workflows/agent-macro.yml` calls
  `Kghaffari26/agents-core/.github/workflows/run-agent.yml@v0.1.0` with `secrets: inherit`,
  `agent: macro`, `max_run_usd: "0.25"` and `site_repo: Kghaffari26/agents-hub`. It keeps both crons.
  - A stdlib-only `gate` job (`scripts/fomc_gate.py`) ends the 19:30 UTC run before `uv sync` unless today
    is an FOMC decision day in `config/fomc_dates.toml`.
  - actionlint passes.
- **Live FRED verification**: all 25 series verified (table below).
- **Real FOMC fixtures**: six real statement pages from 2022, 2024 and 2026 replaced the hand-built ones,
  along with the real minutes page, calendar page and RSS feed. The real 2026 page layout broke the old
  parser, which is now fixed:
  - the "by a 9 – 3 vote" preface that names only dissenters;
  - "raise … by 1/4 percentage point to";
  - words glued together by `text(strip=True)`;
  - non-breaking hyphens.
- **FRED fixtures** are now real recorded responses. One of them is the Oct 2025 `"."` shutdown gap in CPIAUCSL.
- **`config/fomc_dates.toml`** checked against the live calendar page: the 2026 dates were right. The 2027
  meetings were added.
- **Evals re-run with real LLM calls** (details below). The FOMC tone labels stay **PROVISIONAL**.
- **One real run, then an immediate re-run** (numbers below).

## Test count

**284 tests passing**, and `uv run ruff check .` is clean. The suite makes no live network calls.
`tests/test_agent.py` runs the agent end to end through the real agents-core runner, with a mock
FRED/Fed transport and a fake Anthropic client. It covers each §13 item:

- the publish contract;
- zero LLM calls on an immediate re-run;
- the payroll revision block and its bullet;
- a forced guard failure falling back to the template;
- a refusal falling back to the template;
- the dry run;
- the cost cap keeping the previous `latest.json`.

## Real run cost and size

From `data/costs.jsonl`:

| Run | LLM calls | Cost | `latest.json` |
|---|---|---|---|
| `2026-09-26T18-14-55Z-b17c63` (first run, fresh state) | brief $0.0082 + FOMC read $0.0107 + minutes $0.0293 | **$0.0481** | 202,569 bytes (limit 350 KB) |
| `2026-09-26T18-15-56Z-ec1cae` (immediately after) | **0** | **$0.0000** | same; `data_changed: false`, brief `reused_from_run_id` = the run above |

The first run is a worst case: it summarizes the latest statement and minutes as well as writing the brief.
A normal release day is the brief alone, about $0.008. Every guarded call passed on its first attempt.

**Anthropic spend this session: $0.19.**
- An earlier real run on pre-fix code: $0.0477. It is in `data/costs.jsonl`. Its state was reset before
  the final runs.
- The final real run: $0.0481.
- The evals: $0.0976.
- Tests use a fake client.

## Live FRED verification (`scripts/verify_macro_series.py`, 2026-09-26)

All 25 configured series resolved. Excerpt:

| id | series | frequency | units | last_updated | last obs |
|---|---|---|---|---|---|
| cpi | CPIAUCSL | Monthly | Index 1982-1984=100 | 2026-09-11 | 2026-08 = 334.131 |
| core_cpi | CPILFESL | Monthly | Index 1982-1984=100 | 2026-09-11 | 2026-08 = 337.765 |
| pce / core_pce | PCEPI / PCEPILFE | Monthly | Index 2017=100 | 2026-08-26 | 2026-07 |
| breakeven_5y | T5YIE | Daily | Percent | 2026-09-25 | 2.34 |
| unrate | UNRATE | Monthly | Percent | 2026-09-04 | 2026-08 = 4.1 |
| payrolls | PAYEMS | Monthly | Thousands of Persons | 2026-09-04 | 2026-08 = 159,075 |
| claims | ICSA | Weekly, Ending Saturday | Number | 2026-09-24 | 2026-09-19 = 197,000 |
| jolts | JTSJOL | Monthly | Level in Thousands | 2026-09-01 | 2026-07 = 7,271 |
| ahe | CES0500000003 | Monthly | Dollars per Hour | 2026-09-04 | 37.75 |
| gdp | A191RL1Q225SBEA | Quarterly | Percent Change | **2026-07-30** | 2026-Q2 = 1.5 |
| retail_sales / industrial_production | RSAFS / INDPRO | Monthly | $M / Index | 2026-09-16 / 09-18 | 2026-08 |
| fed_funds_upper / lower | DFEDTARU / DFEDTARL | Daily, 7-Day | Percent | 2026-09-26 | 4.00 / 3.75 |
| effr, treasury 3M/2Y/5Y/10Y/30Y | EFFR, DGS* | Daily | Percent | 2026-09-25 | 10Y = 5.18 |
| curve_10y2y / curve_10y3m | T10Y2Y / T10Y3M | Daily | Percent | 2026-09-25 | 0.36 / 0.93 |
| mortgage_30y | MORTGAGE30US | Weekly, Ending Thursday | Percent | 2026-09-24 | 7.03 |
| umich_sentiment | UMCSENT | Monthly | Index 1966:Q1=100 | 2026-09-25 | 2026-08 = 51.7 |

JOLTS is published in thousands, so `config/macro.toml` now divides by 1000 to display millions.

## Eval results

Full detail is in `docs/agents/macro.md` and `evals/results/macro-2026-09-26.json`. The eval run cost $0.0976.

| Eval | Result |
|---|---|
| Number fidelity | 100% of final bullets pass the guard; 100% on the first attempt (5 fixtures) |
| Event grounding / style (LLM bullets) | 100% / 100% |
| FOMC tone | 83% (5/6); the near-identical pair came back `unchanged`. Labels are **PROVISIONAL** |
| Phrase verbatim | 100% raw and after filtering |

## Things to do by hand

1. **Repository secrets** (Settings → Secrets and variables → Actions). The workflow uses `secrets: inherit`.
   - `FRED_API_KEY` and `ANTHROPIC_API_KEY` are required.
   - `SITE_DISPATCH_TOKEN` is optional. Without it, run-agent.yml quietly skips notifying `agents-hub`.
2. **Actions permissions**. Under Settings → Actions → General → Workflow permissions, choose "Read and
   write". run-agent.yml commits `data/` to `main` and force-pushes the `data` branch. If `main` gets
   branch protection, let the Actions bot push, or the state commit fails.
3. **If `Kghaffari26/agents-core` is private**, go to its Settings → Actions → General → Access and allow
   repositories owned by the account, or the `uses:` line won't resolve.
4. **Confirm the FOMC tone labels** in `evals/macro/labels_proposed.json` and then flip `status`.
   Two labels changed after reading the real text.
   - The 2023-12 → 2024-01 pair is genuinely arguable, and it is the one the model missed.
5. **GDP shows `delayed: true`**. FRED's `A191RL1Q225SBEA` was last updated 2026-07-30, but FRED's calendar
   for the GDP release lists 2026-08-26. By the §5.5 rule that's a delay. Check whether the second estimate
   really hasn't posted. If the calendar entry is some other BEA product, the rule needs a per-release
   exception.

## agents-core: gaps found (none blocking; agents-core not modified)

- **`run-agent.yml` doesn't restore the previous `public-data/`**. The orphan `data` branch would hold one
  `history/` file, and `ctx.previous_latest()` is always None in CI.
  - Workaround: the no-change path runs off committed `data/macro/state.json`.
  - `agent-macro.yml` passes `cache_path: .cache/macro` plus `public-data`, so actions/cache keeps the
    observation history and previous output.
  - A native restore of the `data` branch would be cleaner.
- **`RunMeta` has no `warnings` field**. §10's "`status: ok` with a warning" is logged, not published.
  `status: "stale"` is used when a FRED series fails.
- **`agents_core.llm` has no `temperature` parameter** (§7.1 asks for 0). This is moot for
  `claude-sonnet-5`, which rejects sampling parameters.
- **§10's "open a GitHub issue when statement extraction fails"** has no hook in run-agent.yml. It is
  logged as an error, and the previous FOMC block is kept.

## Not done

- `/macro` page rendering (§13's last item) lives in `Kghaffari26/agents-hub`, not in this repo.
- BLS fallback (`use_bls_fallback`) stays off, as the spec defaults it.
