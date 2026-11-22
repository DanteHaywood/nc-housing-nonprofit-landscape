# Database schema

The project's database is one DuckDB file at `config.yaml: paths.processed`
(`data/processed/landscape.duckdb`). It holds three schemas, one per owner (D-014):

| Schema | Written by | Rebuilt | Read by |
|---|---|---|---|
| `ingest` | `python -m src.ingest.build` | every build, atomically | classify, eval, agent, `src.ingest.quality` |
| `classify` | `src/classify/` *(planned)* | by classify runs only | eval, agent |
| `agent` | `src/agent/` *(planned)* | by the agent setup only | the agent's read-only connection |

A build drops and recreates `ingest` inside one transaction. A failed build
leaves the previous tables in place, and `classify` and `agent` are never
touched. No foreign key crosses schemas, so the drop can't be blocked or
cascade into labels.

**Conventions.**
- EINs are 9-character strings. OBJECT_IDs are 18-character strings.
- Dollar amounts are whole-dollar `BIGINT`s.
- NULL means "not reported". The IRS often omits a field when it is zero, so whether NULL counts as 0 is decided per query, in SQL (D-012).
- Column names are lowercase snake_case.

The DDL for `ingest` is `src/ingest/schema.sql`. It is the source of truth for
column types and comments, and `tests/test_build.py` checks that it matches
what `src/ingest/parse.py` produces.

## `ingest`

```
organizations (ein) ──< filing_index (object_id) ──1:0..1── filings (object_id)
                                                              ├──< programs
                                                              ├──< revenue_lines
                                                              └──< schedule_o
latest_filings = one row of filings per ein (view)
funnel         = row counts per pipeline step (standalone)
```

| Table | One row per | Key | Notes |
|---|---|---|---|
| `organizations` | organization in the state BMF, candidate or not | `ein` | NTEE code and major group, `candidate_group` (`ntee_L`, `ntee_adjacent`, or NULL), HQ address with `zip5`, BMF asset/income/revenue. The NTEE baseline, the frame for the eval's random sample (D-009), and the agent's directory. **The address is headquarters, not service area (D-006).** |
| `filing_index` | filing the manifest selected (latest per EIN and tax period, D-010) | `object_id`; unique (`ein`, `tax_period`) | `status`: `parsed`, `unsupported_form` (990-PF), `not_in_zip`, `unreadable`, `not_extracted`, `parse_failed`. `zip_name` is the zip the XML was actually found in, while `xml_batch_id` is what the index claimed (D-011). |
| `filings` | parsed 990 or 990-EZ | `object_id`; unique (`ein`, `tax_period`) | Mission, Part I activity line (990), and financials (D-012, D-013). 990 and 990-EZ share a column when the concept matches, and a column a form lacks is NULL for it. `tax_year` is the IRS `TaxYr` (the year the period *began*); use `tax_period` (YYYYMM the period ended) for ordering and trends. |
| `programs` | Part III program | (`object_id`, `program_number`) | Description and expense/grant/revenue amounts. `part_iii_line` is 4a–4d on the 990, NULL on the 990-EZ. |
| `revenue_lines` | 990 Part VIII line 2 revenue stream | (`object_id`, `line_number`) | Description and amount. Program service revenue by source, for funding mix. |
| `schedule_o` | Schedule O note | (`object_id`, `entry_number`) | The filer's free-text `reference` and `explanation`, plus a derived `part` and `is_mission_or_program` (D-013). |
| `funnel` | pipeline step | `step` | Row counts recorded at build time, including steps that leave no table behind (rows dropped as 990-T, rows removed by dedupe). `built_at` dates the build. |
| `latest_filings` (view) | EIN with a parsed filing | `ein` | Its most recent filing by `tax_period`. The default classifier input and the agent's "current size". |

Typical joins:
- `filings JOIN organizations USING (ein)`: financials with NTEE and location.
- `latest_filings JOIN programs USING (object_id)`: what each organization does now.
- `filing_index LEFT JOIN filings USING (object_id)`: coverage, including filings that weren't parsed.

## `classify` *(planned, D-014)*

| Table | One row per | Key | Notes |
|---|---|---|---|
| `classifications` | organization × prompt version × model | (`ein`, `prompt_version`, `model`) | `label`, `reason`, `object_id` (the filing whose text was used), `text_source` (mission / schedule_o / activity_or_mission), `status` (ok / invalid_label / api_error), `created_at`. Rebuildable from `data/interim/api_cache/`, which stays the source of truth. |
| `ntee_taxonomy_map` | NTEE code prefix | `ntee_prefix` | The NTEE baseline's mapping onto the project taxonomy. Needs its own decision entry (D-004). |

Hand labels stay in `evals/labels/` as committed CSVs. They are not loaded into
the database, so the agent never sees them.

## `agent` *(planned, D-014)*

Views over `ingest` and `classify` with plain names for the agent (for example,
one organization profile: latest filing, label, and location). The agent's
connection is read-only, allows `SELECT` only, and caps results at 500 rows. Query
logs are written to files next to each brief, not to the database.
