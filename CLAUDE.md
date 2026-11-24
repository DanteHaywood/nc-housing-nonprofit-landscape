# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Classifies North Carolina housing nonprofits from public IRS Form 990 data using the Claude API, and evaluates the classifier against hand labels and the IRS's own NTEE codes (Stage 1). A read-only SQL agent then writes landscape briefs from the cleaned data (Stage 2). This is a portfolio project, so code clarity and honest results matter more than features. Background and data sources are in `README.md`.

**Status:** ingest has started. `src/ingest/bmf.py` loads the state BMF and filters candidate organizations (tested in `tests/test_bmf.py`). `src/ingest/manifest.py` joins the candidates to the yearly index CSVs, dedupes the filings, and writes `data/interim/manifest.parquet` (tested in `tests/test_manifest.py`). `src/ingest/extract.py` extracts only the manifest's XML to `data/interim/xml/`, using 7-Zip for Deflate64 zips (tested in `tests/test_extract.py`). `src/ingest/parse.py` parses 990 and 990-EZ XML per `docs/field_map.md` into `filings`, `programs`, `revenue_lines`, and `schedule_o` parquet tables in `data/interim/` (tested in `tests/test_parse.py`). `src/ingest/build.py` runs all four stages and loads the `ingest` schema of the DuckDB database (`src/ingest/schema.sql`, `docs/schema.md`, D-014), and `src/ingest/quality.py` prints the funnel and missing-field shares (tested in `tests/test_build.py` and `tests/test_quality.py`). `evals/make_sample.py` has drawn the labeled sample and written blank sheets to `evals/labels/` (D-015), and the build ingests the sample's EINs. Open items: Cape Fear filtering needs a ZIP-to-county crosswalk (D-014). Open item for `src/classify/`: missions that only say "See Schedule O" need a rule to pick replacement text from `schedule_o` and `activity_or_mission` (D-013). `src/classify/prompts/v001.md` is the first classifier prompt: the six labels as XML categories, in the format of Anthropic's classification cookbook (D-016). `src/classify/classifier.py` runs a Claude classifier (one forced, strict tool call; cached per EIN, prompt version, and model) and a random baseline on any labeling-sheet CSV, and `src/classify/run_dev.py` runs both on the dev sheet into `data/processed/` (tested in `tests/test_classifier.py`, D-016). `evals/classify_eval.py` scores Claude, the NTEE baseline (mapped by `evals/ntee_taxonomy_map.csv`, D-018), and the random baseline against the hand labels, and writes `evals/results/classify_{split}__{prompt_version}__{model}.{json,md,csv}` (tested in `tests/test_classify_eval.py`). `src/classify/run.py` and `src/agent/` are not written yet, and the layout in `README.md` is the target. The run commands below won't work until their modules exist. `evals/agent_eval.py` and `docs/error_analysis.md` are planned, not written.

## Commands

```bash
pip install -e ".[dev]"            # install with dev tools
ruff format . && ruff check --fix .  # format and lint
pytest                             # run tests
pytest tests/test_<module>.py::test_<name>  # run a single test
python -m src.ingest.build         # build the DuckDB database from data/raw/
python -m src.ingest.quality       # print the ingest funnel and missing-field shares
python evals/make_sample.py draw   # draw the labeled sample (then rebuild, then `sheet`)
python -m src.classify.run         # classify organizations (calls the API)
python evals/classify_eval.py      # Stage 1 eval
python -m evals.plot_confusion     # README confusion-matrix PNGs (needs the viz extra)
```

A hook in `.claude/settings.json` runs Ruff on every Python file you edit. Fix any lint errors it reports before moving on. Run `pytest` before calling a task done.
Development is on Windows. The key goes in `.env` at the repo root (gitignored; template in `.env.example`), or in PowerShell with `$env:ANTHROPIC_API_KEY = "..."`, not `export`. A shell value wins over `.env`.

## Pipeline

1. **Ingest** (`src/ingest/`): IRS BMF filtered to `config.yaml: state` → candidate EINs → join to the yearly index CSVs for `filing_years`, drop 990T, dedupe to the latest filing per EIN and tax period by `OBJECT_ID` (D-010) → `data/interim/manifest.parquet` → extract only the manifest's XML to `data/interim/xml/`, located by each zip's file list, not `XML_BATCH_ID` (D-011) → parse with `lxml` per `docs/field_map.md` into filings, programs, revenue-line, and Schedule O tables (990-PF skipped, missing fields None, D-012, D-013) → load the `ingest` schema of the DuckDB file at `paths.processed`, atomically (D-014). Tables and keys: `docs/schema.md`.
2. **Classify** (`src/classify/`): read mission and program service text from DuckDB, call `config.yaml: model`, and write one label per organization to the `classify` schema (never to `ingest`, which every build replaces).
3. **Agent** (`src/agent/`): answers landscape questions with read-only SQL over views in the `agent` schema, Stage 1 labels included.

The `config.yaml` keys are `model`, `paths.{raw,interim,processed,labels}`, `state`, `filing_years`, `label_sample_size`, `label_sample` (seed, group sizes, reserves, dev size; D-015), and `ntee_adjacent` (NTEE codes ingested alongside group L, D-009). `src/config.py: load_config()` reads it.

## Taxonomy

There is one primary label per organization: `emergency_shelter`, `affordable_housing_development`, `homeownership`, `rental_assistance`, `supportive_housing`, `not_housing`. D-008 was reviewed after the 40 dev labels and accepted unchanged (D-017). The labels are strict on primary purpose and can miss organizations doing real housing work outside it; widening them is a tradeoff to settle with the client. Any change goes through the decision log.

## Layout

- `src/ingest/`: BMF filter → index join and dedupe → selective unzip → XML parse → DuckDB (`build.py`, `schema.sql`), plus `quality.py`. New data-loading code goes here.
- `src/classify/`: prompts, API calls, label output. Prompts live in `src/classify/prompts/` as versioned files (`v001.md`, `v002.md`, ...). Each version embeds its own `<categories>` block and follows the cookbook format (D-016).
- `src/agent/`: SQL tool, agent loop, brief writer.
- `evals/`: eval scripts, `evals/labels/` (hand labels and labeling guide), and `evals/results/` (saved eval output, the only source for numbers in `README.md`).
- `docs/decisions.md`: decision log. `docs/schema.md`: database tables and keys. `docs/field_map.md`: XML paths parsed. `docs/error_analysis.md`: what the model got wrong.
- `tests/`: pytest tests. `tests/fixtures/`: small sample files they use.
- `data/`: raw, interim (including `data/interim/api_cache/`), and processed data. Never committed.
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

- Read `ANTHROPIC_API_KEY` from the environment. CLI entry points load `.env` into it first with `src/config.py: load_env()`; library code never reads the file. Never write keys to code, config, logs, or output, and never commit `.env`.
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
- The labeled set is about 100 organizations statewide (`label_sample_size`), sampled on purpose from three groups: NTEE major group L, adjacent human services and community development codes, and a random sample. `evals/make_sample.py` draws it (40/30/30) and splits it into a 40-org dev set and a 60-org test set (D-015). Prompt work looks only at dev. The labeling sheets are blind (no NTEE code or sample group). `evals/labels/sample.csv` and the label CSVs are never overwritten. Labeling follows `evals/labels/labeling_guide.md`.
- NTEE codes are mapped onto the taxonomy by `evals/ntee_taxonomy_map.csv` (D-018). Vague group L codes are `unmapped` and scored wrong. Changing the map needs a new decision entry.
- Stage 2 briefs focus on the Cape Fear region. They must say that BMF addresses are headquarters, not service area (D-006).

## Decision log

When a task involves a real design choice (a library, schema, taxonomy, prompt approach, or eval method), add an entry to `docs/decisions.md` using the template at the top of that file. Entries are numbered `D-00X`, newest at the bottom. Accepted entries are not edited; a later entry supersedes them, and the old one is marked `Superseded by D-00X`.

## Workflow

- For changes touching more than one module, outline the plan first and wait for approval.
- Read existing code in a module before changing it.
- Keep commits small, with messages saying what changed and why.
- `README.md` restates the project's data, API, agent, and eval rules for readers. When one of those rules changes here, update `README.md` in the same change.
