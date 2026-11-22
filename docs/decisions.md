# Decision Log

Design decisions for this project, one entry per decision, newest at the bottom. Format follows Michael Nygard's lightweight Architecture Decision Record (Title, Status, Context, Decision, Consequences), plus a short Alternatives section.

**Rules**
- Write an entry when a choice would be hard to reverse or someone would reasonably ask "why did you do it this way?"
- Keep each entry short: a few sentences per section.
- Don't rewrite accepted entries. To change course, add a new entry and mark the old one `Superseded by D-00X`.
- Status values: `Proposed`, `Accepted`, `Superseded`, `Rejected`.

## Template

```markdown
## D-00X: [Short noun phrase]

**Status:** Proposed | Accepted | Superseded by D-00Y | Rejected
**Date:** YYYY-MM-DD

**Context.** What problem or constraint forced a choice?

**Decision.** What we're doing.

**Alternatives.** What else was considered, and why not.

**Consequences.** What gets easier, what gets harder, what we'll watch for.
```

---

## D-001: Python for the whole project

**Status:** Accepted
**Date:** 2026-11-20

**Context.** The project needs XML parsing, data handling, Claude API calls, and an agent loop. Readers include interviewers at AI companies.

**Decision.** Use Python throughout, with the official Anthropic Python SDK.

**Alternatives.** R for data prep (the author's strongest language) with Python only for the API. Rejected: two languages add handoff code and make the repo harder to follow.

**Consequences.** One toolchain, one test suite. Data prep takes longer to write than it would in R.

## D-002: Parse 990 XML directly with lxml

**Status:** Accepted
**Date:** 2026-11-20

**Context.** Only a narrow set of fields is needed (identifiers, mission and program descriptions, core financials), for one state and one subsector.

**Decision.** Parse the needed fields directly with `lxml`, rather than building full relational tables from every schedule.

**Alternatives.** The `ef2` R package from the Nonprofit Open Data Collective. Rejected per D-001, and it produces far more tables than this project needs.

**Consequences.** Less code and faster runs. Field paths must be checked by hand against the IRS schemas, and tests must cover changes across schema years.

## D-003: Ruff for formatting and linting

**Status:** Accepted
**Date:** 2026-11-20

**Context.** The code needs a consistent, widely recognized style that Claude Code can apply automatically.

**Decision.** Ruff formatter (Black-compatible, 88-character lines) and Ruff linter with rules `E`, `F`, `I`, `UP`, `B`, `SIM`.

**Alternatives.** Black plus flake8 plus isort. Same style, more tools.

**Consequences.** One tool and one config block in `pyproject.toml`.

## D-004: NTEE codes are the eval baseline

**Status:** Accepted
**Date:** 2026-11-20

**Context.** An accuracy number means little without a comparison. NTEE codes are what funders and researchers use today to find organizations by subject.

**Decision.** Score both Claude's labels and NTEE codes against the same hand-labeled set, and always report them side by side.

**Alternatives.** Report Claude's accuracy alone. Rejected: it doesn't show whether Claude adds anything.

**Consequences.** NTEE codes must be mapped to the project taxonomy for comparison. That mapping is itself a judgment call and gets its own entry when it's made.

## D-005: All arithmetic in SQL, not in the model

**Status:** Accepted
**Date:** 2026-11-20

**Context.** Landscape briefs will quote revenue, counts, and trends. Funders may act on them, so every figure has to be traceable.

**Decision.** The agent gets a read-only SQL tool. Numbers come only from query results. The model explains them and never calculates them.

**Alternatives.** Give the model raw tables and let it summarize. Rejected: figures can't be verified.

**Consequences.** Enables an automated grounding eval. Some questions will need more queries per brief.

## D-006: North Carolina for the eval, Cape Fear region for the brief

**Status:** Accepted
**Date:** 2026-11-20

**Context.** The Cape Fear counties may have too few housing organizations for a solid labeled set.

**Decision.** Sample about 100 organizations statewide for labeling and evaluation. Focus the Stage 2 landscape brief on the Cape Fear region.

**Alternatives.** Cape Fear only (too small a sample); national (too much to label carefully).

**Consequences.** BMF addresses reflect headquarters, not service area, so regional briefs must say so.

## D-007: DuckDB as the analytical database

**Status:** Proposed
**Date:** 2026-11-20

**Context.** Needs a database that is easy to set up, works well with pandas, and can be opened read-only for the agent.

**Decision.** DuckDB, as a single file in `data/processed/`.

**Alternatives.** SQLite (weaker for analytical queries); MySQL (needs a server, harder for a reviewer to run).

**Consequences.** A reviewer can run the project with no database setup. To confirm once ingest is built.

## D-008: Housing taxonomy, first draft

**Status:** Proposed
**Date:** 2026-11-20

**Context.** The classifier needs a fixed set of labels that is finer than NTEE major group L but small enough to hand-label consistently.

**Decision.** Six labels: `emergency_shelter`, `affordable_housing_development`, `homeownership`, `rental_assistance`, `supportive_housing`, `not_housing`. One primary label per organization.

**Alternatives.** Multi-label classification. Deferred: harder to label and evaluate in the first pass.

**Consequences.** Organizations doing several things get a single primary label. Revisit after labeling the first 25 organizations.

## D-009: NTEE codes for the candidate set

**Status:** Proposed
**Date:** 2026-11-20

**Context.** Ingest needs a list of candidate EINs from the BMF before joining to the 990 index. Housing work is not limited to NTEE group L: shelters, group homes, and community development corporations are often coded under human services (P) or community improvement (S). This entry is about which organizations to ingest. It is not the NTEE-to-taxonomy mapping that D-004 calls for, which still needs its own entry.

**Decision.** Candidates are every organization in NTEE major group L, plus these adjacent codes: P60 (emergency assistance), P70, P73, P75 (residential care, group homes, senior continuing care), P85 (homeless centers), S20, S21, S22 (community and neighborhood development), S30, S31 (economic and urban development). Codes match on their first three characters, so `P73Z` matches `P73`. The list lives in `config.yaml: ntee_adjacent`. Rows with a blank NTEE code are excluded here. In the NC BMF this gives 2,593 candidates (1,047 L, 1,546 adjacent).

**Alternatives.** Adding P20 (multipurpose human services) would add 2,064 organizations, mostly not housing. Taking all of P and S would add chambers of commerce, service clubs, and similar groups. Both would cost more to ingest for little gain.

**Consequences.** Housing organizations coded P20 or left uncoded (about a quarter of NC BMF rows have no NTEE code) are missed by this filter. The random-sample group in the labeled set is where those misses show up, so that sample must be drawn from the full BMF, not from these candidates.

## D-010: Manifest dedupe key and return types

**Status:** Proposed
**Date:** 2026-11-22

**Context.** The yearly index CSVs list more than one filing for some EIN and tax period pairs (amendments, resubmissions, and the same period showing up in both years' indexes). Ingest keeps one filing per pair. The index's `SUB_DATE` column only holds the processing year (`"2024"` on every 2024 row), so it can't order filings within a year. The index also lists 990-T returns, which report unrelated business income and have no mission or program text.

**Decision.** `src/ingest/manifest.py` dedupes on the index, before any XML is extracted, so duplicate filings are never unzipped. It keeps the row with the highest `OBJECT_ID` per EIN and tax period, across all `filing_years`. `OBJECT_ID` is filled on every row, is always 18 digits, and starts with the processing year, so the highest value is the most recently processed filing. It is sorted as a fixed-width string, and the loader raises if any value isn't 18 digits. 990-T rows are dropped before the dedupe. 990, 990-EZ, and 990-PF are kept. The manifest is written to `paths.interim/manifest.parquet` through DuckDB, which avoids adding pyarrow as a dependency.

**Alternatives.** Ordering by `SUB_DATE` was ruled out because it carries no information within a year. Deduping on EIN, tax period, and return type would keep 990-T rows as separate filings, but nothing downstream uses them. Keeping 990-T rows and deduping on EIN and tax period alone can pick a 990-T over the organization's real 990.

**Consequences.** The rule relies on `OBJECT_ID` order matching IRS processing order. That holds for these two years, but should be checked again if older or newer index formats are added. In the NC run, 1,044 of 2,593 candidates have at least one e-filed return in 2024–2025, giving 2,040 filings (1,486 990, 493 990-EZ, 61 990-PF). Candidates without one are probably 990-N filers or have no e-filed return in these years.

## D-011: Locating and extracting filings from the IRS zips

**Status:** Proposed
**Date:** 2026-11-22

**Context.** Ingest has to pull 2,040 manifest filings out of 28 zip partitions (about 1.6 million members) without extracting any zip in full. The index's `XML_BATCH_ID` names a zip for each filing, but 413 of the 2,040 are actually in a different zip. Three 2025 zips (`05A`, `05B`, `11B`) use Deflate64 compression. Python's `zipfile` can list them but can't decompress them, and 272 manifest filings are only in those zips. Windows' built-in `tar.exe` fails on them ("Damaged Zip archive").

**Decision.** `src/ingest/extract.py` reads every zip's file list and maps each `OBJECT_ID` to the zip that actually holds it, ignoring `XML_BATCH_ID`. Members compressed with a method `zipfile` supports are extracted with `zipfile`. Others (Deflate64) are extracted with the 7-Zip command-line tool, found on PATH or in its default Windows install folder, in one call per zip. Files are written to `paths.interim/xml/<OBJECT_ID>_public.xml`, through a temporary file or staging folder, so an interrupted run never leaves a partial file under its final name. A file that already exists with the member's uncompressed size is skipped. Filings found in no zip are listed in `paths.interim/extract_missing.csv`. If 7-Zip is missing, Deflate64 members are reported as unreadable instead of crashing.

**Alternatives.** Trusting `XML_BATCH_ID` would have missed 413 filings. The `zipfile-deflate64` package would keep everything in Python, but it adds a dependency that is untested on Python 3.15 and may need a C compiler to install. Skipping Deflate64 zips would have dropped 13% of filings.

**Consequences.** Anyone rebuilding the data needs 7-Zip installed, which `README.md` now says. A tool outside Python is part of ingest, so a 7-Zip failure is logged with its error text and its filings are counted as unreadable. Scanning all 28 file lists takes about 7 seconds, so reruns stay fast. In the NC run, all 2,040 filings were found and extracted, and every file's filer EIN matches the manifest.

## D-012: Parsing 990 and 990-EZ XML into three tables

**Status:** Proposed
**Date:** 2026-11-22

**Context.** Classification and the landscape brief need each filing's mission, program descriptions, and headline financials in tables. `docs/field_map.md` lists the XML paths for the 990 and 990-EZ. The two forms use different tags for the same concepts, and each has fields the other lacks. The IRS usually leaves a field out when its value is zero, so absent tags are common (for example, 697 of 1,486 990s have no `GovernmentGrantsAmt`). 61 manifest filings are 990-PF, which the field map doesn't cover.

**Decision.** `src/ingest/parse.py` reads each file with `lxml` and writes three tables to `paths.interim`:
- `filings`: one row per filing. 990 and 990-EZ tags that mean the same thing share one column, and a column a form doesn't have is None for that form.
- `programs`: one row per Part III program, with its line (4a–4d) on the 990.
- `revenue_lines`: one row per 990 Part VIII line 2 revenue stream.

990-PF files are skipped and counted. A missing or malformed field becomes None and is logged, one line per filing, to `paths.interim/parse.log`. Parsing never raises on bad data. Missing is kept distinct from zero: None means "not reported", and any rule treating it as 0 belongs in SQL with its own decision. Dollar amounts are ints, stored as BIGINT. Text has its whitespace collapsed. The IRS namespace is read from each file's root, so a file without it parses too.

Changes to the field map: the 990-EZ salaries tag is `SalariesOtherCompEmplBnftAmt` (the map had `...EmplBnftsAmt`, which matched none of 493 filings). `ReturnTypeCd` is added, and so are the per-program `GrantAmt`, plus `ExpenseAmt` and `RevenueAmt` for "other" (4d) programs, to match lines 4a–4c.

**Alternatives.** Filling missing amounts with 0 at parse time would hide the difference between "zero" and "not reported". Separate tables per form would make every downstream query handle two schemas. Parsing 990-PF with no field map would produce rows that are mostly None.

**Consequences.** Analysis SQL must decide, per question, whether a NULL amount counts as 0. `tax_year` is the IRS `TaxYr`, the year the tax period began. For the 899 fiscal-year filers it is one less than the `TAX_PERIOD` end year. In the NC run, 1,979 filings parsed (1,486 990, 493 990-EZ) with 2,969 program rows and 2,317 revenue lines. None failed, and none had a malformed amount. 33 missions just point to Schedule O, which isn't parsed yet.
