# financedatabase-runner

Runs [FinanceDatabase](https://github.com/JerBouma/FinanceDatabase)
(`financedatabase`) queries via GitHub Actions — part of a
one-public-repo-per-tool pattern: each tool gets its own repo, the
commands to execute live in a repository variable, runs are triggered
manually, and outputs are collected as artifacts.

FinanceDatabase is a free categorisation database of 300,000+ symbols —
Equities, ETFs, Funds, Indices, Currencies, Cryptocurrencies and Money
Markets — giving the products that exist in each country, sector and
industry. It is explicitly *not* a price/fundamentals feed; it answers
"which symbols exist, where, and in which sector/industry".

## How to run a query

1. **Edit the commands**: repo *Settings → Secrets and variables →
   Actions → Variables* → set `COMMANDS` to the shell commands this run
   should execute, e.g.

   ```sh
   mkdir -p results
   python scripts/query.py 2>&1 | tee results/run.log
   ```

   The same can be done through the GitHub API
   (`PUT /repos/{owner}/{repo}/actions/variables/COMMANDS`).
2. **Trigger manually**: *Actions* tab → “Run analysis” → *Run workflow*
   (or `POST /repos/{owner}/{repo}/actions/workflows/run.yml/dispatches`).
   The workflow has **no other triggers** — `workflow_dispatch` only.
3. **Collect results**: download the `results` artifact from the
   finished run. It contains the query CSVs, the options files,
   `summary.json` and the run log. The upload step is `if: always()`,
   so partial results survive a failing step.

The workflow itself is generic: checkout → Python 3.11 →
`pip install -r requirements.txt` → run `${{ vars.COMMANDS }}` →
upload `results/`. To run a different query, change the variable or
the script's CLI arguments — not the workflow.

## The script

`scripts/query.py` is generic and market-agnostic: every filter is a
CLI argument, nothing is hardcoded beyond the pilot defaults.

```sh
python scripts/query.py \
  --egypt-country Egypt \
  --us-country "United States" \
  --us-sector "Information Technology" \
  --limit 50 --sample 5 --output-dir results
```

It adapts to the installed `financedatabase` version
(`fd.Equities()` + `.select(...)` / `.show_options(...)`, with legacy
`.options(...)` and module-level helpers as fallbacks) and records
counts, 5 sample rows (symbol + name) and the exact error/outcome of
every attempt in `results/summary.json` — including when a filter
value such as a country does not exist in the database.

## Pilot

Default `COMMANDS`:

```sh
mkdir -p results
python scripts/query.py 2>&1 | tee results/run.log
```

Pilot queries:

- **(a) Egypt equities** — `select(country="Egypt")`: how many symbols
  the database holds for Egypt, the full result (or first 50 rows) in
  `results/egypt_equities.csv`, plus 5 samples in `summary.json`. If
  Egypt is not an available country value, the exact outcome and the
  available country options are recorded instead.
- **(b) US sector equities** — `select(country="United States",
  sector="Information Technology")` (the sector name used by
  financedatabase 2.x; a plain `Technology` attempt is recorded as a
  fallback): count + 5 samples, rows in
  `results/us_sector_equities.csv`.
