# CLAUDE.md

## Project

Classifies North Carolina housing nonprofits from public IRS Form 990 data using the Claude API, and evaluates the classifier against hand labels and the IRS's own NTEE codes (Stage 1). A read-only SQL agent then writes landscape briefs from the cleaned data (Stage 2). This is a portfolio project, so code clarity and honest results matter more than features. Background and data sources are in `README.md`.

## Commands

```bash
pip install -e ".[dev]"            # install with dev tools
ruff format . && ruff check --fix .  # format and lint
pytest                             # run tests
python -m src.ingest.build         # build the DuckDB database from data/raw/
python -m src.classify.run         # classify organizations (calls the API)
python evals/classify_eval.py      # Stage 1 eval
```

Run `ruff format`, `ruff check`, and `pytest` before calling a task done.

## Layout

- `src/ingest/`: BMF filter → index join → selective unzip → XML parse → dedupe → DuckDB. New data-loading code goes here.
- `src/classify/`: prompts, API calls, label output. Prompts live in `src/classify/prompts/` as versioned files (`v001.md`, `v002.md`, ...).
- `src/agent/`: SQL tool, agent loop, brief writer.
- `evals/`: eval scripts and `evals/labels/` (hand labels and labeling guide).
- `docs/decisions.md`: decision log. `docs/error_analysis.md`: what the model got wrong.
- `data/`: raw, interim, and processed data. Never committed.
- All paths and the model name come from `config.yaml`. Don't hardcode either.

## Python style

- Python 3.12. Ruff for formatting and linting (Black-compatible, line length 88). Config is in `pyproject.toml`; don't override it inline without a reason in a comment.
- Lint rules: `E`, `F`, `I`, `UP`, `B`, `SIM`.
- Type hints on all function signatures. Short docstrings on public functions saying what goes in and what comes out.
- `pathlib` for paths. f-strings for formatting. `logging`, not `print`, outside of CLI entry points.
- Small, pure functions where possible, so ingest steps can be tested without the real IRS files.
- Ask before adding a new dependency.

## Data rules

- EINs are always strings, zero-padded to 9 digits. Never cast them to integers.
- Dollar amounts are integers (whole dollars), stored as `BIGINT`.
- Never extract a whole IRS zip partition. Join to the index CSV first, then extract only matching files.
- Keep the latest filing per EIN and tax period. Year folders reflect IRS processing date, not tax year.
- Tests use small fixture files in `tests/fixtures/`, never files from `data/`.

## Claude API rules

- Read `ANTHROPIC_API_KEY` from the environment. Never write keys to code, config, logs, or output.
- Cache every API response to `data/interim/api_cache/` as JSONL, keyed by EIN, prompt version, and model. Reruns read from cache unless `--refresh` is passed. This keeps evals reproducible and avoids paying twice.
- Changing a prompt means a new prompt version file, never an edit to an existing one.
- Ask for structured output (one label from the fixed taxonomy plus a short reason). Validate it; anything outside the taxonomy is logged as an error, not coerced.
- Before running anything that calls the API on more than ~20 organizations, say how many calls it will make and wait for a go-ahead.

## Agent rules (Stage 2)

- The SQL tool uses a read-only DuckDB connection, allows `SELECT` only, and caps results at 500 rows.
- All arithmetic happens in SQL. The model explains figures; it never computes them.
- Log every query the agent runs alongside the brief, so the grounding eval can check each number.

## Eval rules

- Never edit hand labels to match model output. A label change needs an entry in `docs/decisions.md` saying why.
- Always report the NTEE baseline next to Claude's results.
- Results in `README.md` come only from saved eval output in `evals/results/`. Never write estimated or illustrative numbers.

## Decision log

When a task involves a real design choice (a library, schema, taxonomy, prompt approach, or eval method), add an entry to `docs/decisions.md` using the template at the top of that file. Accepted entries are not edited; a later entry supersedes them.

## Workflow

- For changes touching more than one module, outline the plan first and wait for approval.
- Read existing code in a module before changing it.
- Keep commits small, with messages saying what changed and why.
