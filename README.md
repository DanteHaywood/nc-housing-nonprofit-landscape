# NC Housing Nonprofit Landscape

**Using Claude to help funders and nonprofits understand who is working on housing in North Carolina, using public IRS Form 990 data.**

> **Status: in progress (started November 2026).** Stage 1 (classification and eval) is being built now. Stage 2 (landscape agent) comes next. Results sections below are placeholders until the evals have run. No numbers in this README are estimates.

---

## The problem

A program officer at a community foundation wants to know: *Who in our region works on housing? How big are they, how are they funded, and where are the gaps?*

The public data to answer this exists: every e-filed Form 990 is released by the IRS. But it is hard to use:

- **The IRS's subject codes are unreliable.** Nonprofits are tagged with National Taxonomy of Exempt Entities (NTEE) codes, and housing falls under major group L. Those codes are coarse, often out of date, and sometimes wrong. Some organizations coded as housing aren't housing organizations, and some housing organizations (community development corporations, for example) are coded elsewhere.
- **The useful detail is in free text.** What an organization actually does is in its mission statement and program service descriptions, not in a code.
- **The raw data is heavy.** Filings come as XML in large zipped monthly partitions.

Most nonprofits and small foundations don't have the staff to do this analysis. This project tests whether Claude can do it reliably, and measures how reliably.

---

## What this project does

### Stage 1: Housing classifier and eval *(in progress)*

Claude reads each organization's mission and program service descriptions from its 990 and assigns it to one housing category:

| Label | Meaning |
|---|---|
| `emergency_shelter` | Emergency or short-term shelter |
| `affordable_housing_development` | Builds, owns, or operates affordable housing |
| `homeownership` | Homebuyer assistance, counseling, self-help building |
| `rental_assistance` | Rent relief, eviction prevention |
| `supportive_housing` | Housing combined with services (disability, recovery, youth, seniors) |
| `not_housing` | Housing is not a primary purpose |

*The taxonomy is a first draft and may change after labeling starts. Changes will be logged in `docs/decisions.md`.*

**The eval.** A hand-labeled set of about 100 North Carolina organizations, sampled on purpose to include:
- organizations NTEE codes as housing (L),
- organizations coded as adjacent human services and community development categories, and
- a random sample.

Claude's labels are compared to the hand labels, and so are the NTEE codes. **The baseline to beat is NTEE.** The write-up covers where Claude is wrong, why, and what changed in the prompt as a result.

### Stage 2: Landscape agent *(planned)*

A Claude agent that answers landscape questions in plain language, e.g. *"Which organizations in the Cape Fear region provide supportive housing, and how has their revenue changed?"*

- Claude gets one tool: a **read-only SQL tool** over the cleaned 990 database, which includes the Stage 1 labels.
- It writes a short brief: who is active, their size, revenue trends, funding mix, and visible gaps.
- **All arithmetic happens in SQL. Claude never calculates figures itself.** Every number in a brief must come from a query result.

**The eval.** An automated grounding check verifies that every number in a brief appears in the query results the agent actually ran. A small set of benchmark questions with known answers checks coverage.

---

## Data

| Source | What it provides | Link |
|---|---|---|
| IRS Form 990 series e-file (XML) | Financials, mission, and program descriptions. Monthly zipped partitions with an index CSV for each year. | [IRS Form 990 series downloads](https://www.irs.gov/charities-non-profits/form-990-series-downloads) |
| IRS Exempt Organizations Business Master File (EO BMF) | EIN, name, address, NTEE code. CSV by state. | [IRS EO BMF](https://www.irs.gov/charities-non-profits/exempt-organizations-business-master-file-extract-eo-bmf) |

**Pipeline.** BMF (North Carolina) → candidate EIN list → join to the yearly index CSVs → extract *only* the matching XML files from each zip → parse with `lxml` → deduplicate to the latest filing per EIN and tax period → load into DuckDB.

**Known limitations of the data**
- **Lag.** Filings run one to two years behind.
- **Small organizations are missing.** Organizations that file the 990-N postcard report no financials or mission text.
- **Location is headquarters, not service area.** BMF addresses generally reflect where an organization is headquartered, which may not be where it works.
- **Grant data has gaps.** Some foundation and grant data is missing from the IRS XML releases ([Aspen Institute comments to the IRS, 2025](https://www.aspeninstitute.org/wp-content/uploads/2025/11/IRS-Comments-2025-Form-990-Series.pdf)).
- **Year folders reflect processing date, not tax year.** A tax year can span two folders.

---

## Tech stack

- **Python** for everything
- **Anthropic Python SDK** ([docs](https://docs.claude.com)) for classification and the agent's tool use
- **lxml** to parse the XML; **pandas** for data handling; **DuckDB** as the analytical database
- **pytest** for pipeline tests

The model name and settings live in `config.yaml`, so results can be rerun on newer models and compared.

---

## Repository layout

*Planned. Will be updated as the code lands.*

```
├── README.md
├── CLAUDE.md               # Instructions for Claude Code when working in this repo
├── config.yaml             # Model, paths, sample sizes
├── data/                   # Not committed (raw IRS files are large)
│   ├── raw/                # BMF CSV, index CSVs, zip partitions
│   ├── interim/            # Extracted XML subset
│   └── processed/          # DuckDB database
├── src/
│   ├── ingest/             # BMF filter, index join, selective unzip, XML parse, dedupe
│   ├── classify/           # Prompt, Claude API calls, label output
│   └── agent/              # SQL tool, agent loop, brief writer
├── evals/
│   ├── labels/             # Hand-labeled set (CSV) and labeling guide
│   ├── classify_eval.py    # Claude vs. hand labels vs. NTEE
│   └── agent_eval.py       # Numeric grounding and benchmark questions
├── docs/
│   ├── decisions.md        # Design choices and why
│   └── error_analysis.md   # What Claude got wrong and what changed
└── tests/
```

---

## How to run

*To be filled in once Stage 1 runs end to end.*

```bash
# 1. Install
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=...

# 2. Put the IRS files in data/raw/ (see Data above)

# 3. Build the database
python -m src.ingest.build

# 4. Classify and evaluate
python -m src.classify.run
python evals/classify_eval.py
```

---

## Results

### Stage 1
*Pending.* Will report: overall agreement with hand labels for Claude and for NTEE, per-category precision and recall, a confusion matrix, and the main error types with examples.

### Stage 2
*Pending.* Will report: grounding-check pass rate and benchmark question results.

---

## Design choices

- **Measure against the existing tool, not against nothing.** NTEE is what people use today, so it's the baseline.
- **Keep math out of the model.** SQL computes the numbers; Claude explains them. That makes every figure traceable.
- **Read-only access.** The agent's database connection can't modify data, and results are row-limited.
- **Start narrow.** One subsector and one state, so the hand-labeled set can be checked carefully.

---

## Limitations of this project

- **One labeler.** I wrote the hand labels myself, and they reflect my judgment. The labeling guide is in `evals/labels/` so others can check or extend it.
- **Small eval set.** About 100 organizations is enough to show patterns, not to give tight error bars.
- **Public data only.** 990s describe what organizations report, not outcomes.

---

## About

Built by Danté Haywood, Director of Data Science at Cape Fear Collective in Wilmington, NC. Built independently on personal time with public data; not a Cape Fear Collective project.

**How AI was used.** I scoped the project, designed the taxonomy, wrote the hand labels, and made the design decisions. I used Claude Code to help write and review code. Claude, through the API, is the subject of the evals.
