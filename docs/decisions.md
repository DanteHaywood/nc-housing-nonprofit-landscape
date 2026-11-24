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

**Status:** Accepted (reviewed in D-017)
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

## D-013: Schedule O and the Part I mission line

**Status:** Proposed
**Date:** 2026-11-22

**Context.** 33 parsed missions mention Schedule O. 18 of them are only the pointer ("SEE SCHEDULE O"), so the classifier would get no mission text for those filings. 36 program descriptions mention it too, 10 as bare pointers. Schedule O is in the XML (`ReturnData/IRS990ScheduleO`) for all 1,486 990s and 448 of 493 990-EZs. It is a list of notes, each with a free-text `FormAndLineReferenceDesc` naming the form line it explains and an `ExplanationTxt`. Of 9,510 notes, 5,590 refer to Part VI (governance). The 990 also has `ActivityOrMissionDesc` (Part I line 1), which D-012 didn't parse.

**Decision.** `src/ingest/parse.py` keeps what was filed and doesn't resolve pointers.
- A `schedule_o` table gets one row per note, governance notes included: `reference` and `explanation` as filed (whitespace-normalized), plus two derived columns.
  - `part` is the form part named in the reference, as a Roman numeral. A regex reads `PART III`, `Part 3`, `Pt VI`, and `Pt. 6`. Part numbers above XII, and references with no part, give None.
  - `is_mission_or_program` is true when `part` is III. When the reference names no part, it is true if the reference mentions mission, program service, accomplishment, or exempt purpose. A reference naming any other part is never flagged, even if it says "program service expenses".
- `filings` gets an `activity_or_mission` column from `ActivityOrMissionDesc`, 990 only.

Choosing classifier text from these sources is left to `src/classify/`, with its own decision entry.

**Alternatives.** Replacing `mission` at parse time would hide what the filer wrote and bury a judgment call in ingest. Keeping only Part III notes would save little space (the full table is 9.5k rows) and drop text the Stage 2 agent might use. Matching keywords on every reference would flag Part IX expense notes that mention "program service expenses".

**Consequences.** In the NC run, 456 notes in 338 filings are flagged mission or program, and 1,024 notes name no part. Most of those are generated labels like `Other Expenses.1`, but 104 read `FORM 990 - ORGANIZATION'S MISSION` and are flagged. Of the 18 bare-pointer missions:
- 10 have a flagged note.
- 3 (990s) have a Schedule O note for Part I line 1.
- 2 (990s) can use `activity_or_mission`.
- 3 (990-EZs) have no mission text anywhere in the filing.

The future selection rule must only treat Part I as the mission on a 990: on the 990-EZ, Part I is revenue and expenses. `activity_or_mission` is filled on all 1,486 990s.

## D-014: Database schema and rebuild

**Status:** Proposed
**Date:** 2026-11-22

**Context.** The ingest stages wrote parquet to `data/interim/`, but nothing loaded `paths.processed`, the database that classify, the eval, and the agent all read. The schema has to serve all three:
- Classify labels organizations and must not lose its labels when ingest is rebuilt.
- The eval needs NTEE codes for every organization, and its random sample comes from the full BMF (D-009).
- The agent runs read-only SQL for briefs, including revenue trends across years.

The parser also read every XML file in `data/interim/xml/`, so files left behind by an older manifest could be loaded.

**Decision.** `python -m src.ingest.build` runs bmf → manifest → extract → parse and loads one DuckDB file with a schema per owner: `ingest` (build), `classify` (`src/classify/`), and `agent` (`src/agent/`). The build replaces `ingest` in one transaction (drop schema, run `src/ingest/schema.sql`, insert), so a failure rolls back to the previous tables. No foreign key crosses schemas, so labels survive every rebuild. `ingest` holds:
- `organizations`: the **full** state BMF (59,487 in NC), flagged with `candidate_group`
- `filing_index`: every manifest filing with a `status` for where it stopped
- `filings`, `programs`, `revenue_lines`, and `schedule_o`, with primary keys
- a `funnel` table of counts recorded at build time
- a `latest_filings` view

The index, not the XML, is the authority for each filing's EIN and tax period. Parse now reads only the manifest's files. `python -m src.ingest.quality` opens the database read-only and prints the funnel and missing-field shares, with all arithmetic in SQL. Columns are described in `docs/schema.md`.

**Alternatives.**
- Candidates only in `organizations`: would leave the eval's random sample and the agent's directory without the 96% of NC organizations outside the candidate NTEE codes.
- A separate database file per stage: the agent would need to attach several files, and joins across them get awkward.
- Rebuilding the whole file each time: would delete labels, or force classify to reload them from cache after every ingest change.
- Keeping parquet as the hand-off: no keys, no constraints, and the agent would query files directly.

**Consequences.** Primary keys and checks catch duplicates and bad statuses at load time. The NC build takes about 15 seconds once XML is extracted. The first build shows the coverage gap clearly:
- 1,044 of 2,593 candidates have a 2024–2025 e-filed return in the index.
- 1,013 have a parsed 990 or 990-EZ (31 filed only a 990-PF).
- For 1,003, the latest filing has mission text that isn't a bare Schedule O pointer.

Two things still need work:
- Organizations in the eval's random sample (D-009) won't have filings until their EINs are added to the manifest's input.
- Cape Fear briefs (D-006) need a ZIP-to-county crosswalk, a new data source, before `organizations.zip5` can be filtered by region.

## D-015: Drawing and splitting the labeled set

**Status:** Proposed
**Date:** 2026-11-22

**Context.** The eval needs about 100 hand-labeled NC organizations (D-006) from three groups: NTEE major group L, the adjacent codes, and a random sample from outside the candidates (D-009). Ingest had only extracted candidates' XML, so all 56,894 non-candidates had no text to label. Prompt iteration also needs held-out data. If the same 100 labels are used to tune the prompt and to report its score, the score is optimistic.

**Decision.** `evals/make_sample.py` draws the sample from DuckDB with a fixed seed (`config.yaml: label_sample`).
- **Groups:**
  - 40 `ntee_L` and 30 `ntee_adjacent`, from organizations with a parsed filing (483 and 530 in the frames)
  - 30 `random`, from non-candidate organizations with a 990 or 990-EZ in the 2024–25 index (14,753), plus 10 ranked reserves
- **Two steps, with an ingest rebuild between them.**
  - `draw` writes `evals/labels/sample.csv`.
  - The build then ingests every EIN in that file, reserves included. A new `ingest.state_index` table holds every state organization's index filings, which is where the random frame comes from.
  - `sheet` takes each group's first organizations in rank order that have a parsed filing. Reserves fill any gaps.
- **Split:** 40 dev and 60 test, in each group's proportions (16/12/12 and 24/18/18), taken in rank order.
- **Blind sheets:** `labels_dev.csv` and `labels_test.csv` carry the filing text (mission, Part I activity line, programs with expenses, mission/program Schedule O notes) and empty `label` and `notes` columns. They leave out the NTEE code and sample group, so hand labels aren't anchored on the baseline they're scored against. Rows are shuffled.
- **Frozen files:** neither step overwrites an existing file. Redrawing or relabeling needs a deliberate delete and a new entry here.
- **Labeling guide:** `evals/labels/labeling_guide.md` is drafted from D-008. Prompt work uses only the dev set.

**Alternatives.** Drawing the random group from organizations already ingested would have meant drawing from candidates only, which defeats its purpose. Drawing from the whole BMF would have picked mostly 990-N filers with no text. A random rather than stratified split could leave a group almost absent from dev. Showing NTEE codes on the sheet would make labeling faster but bias the comparison toward NTEE.

**Consequences.**
- **Frames:** in the NC draw (seed 20261122), all 40 random-group EINs parsed, so no reserves were used. Every sampled organization has mission and program text.
- **Base rate:** the random group is mostly non-housing (education, religion, recreation, and 8 with no NTEE code). Results should be reported by group, not only overall.
- **Proportions:** the sample isn't proportional to the state's population of nonprofits, so its overall accuracy isn't a statewide estimate.
- **Rebuilds:** the build now depends on `evals/labels/sample.csv`. Because the file is frozen, rebuilds stay reproducible.

## D-016: Classifier prompt format

**Status:** Proposed
**Date:** 2026-11-22

**Context.** Stage 1 needs its first prompt. The prompt has to define the six D-008 labels well enough that Claude applies the same boundaries as the labeling guide, return a label the code can validate, and give a short reason for the error analysis. Prompts are cached by version (CLAUDE.md), so the label definitions have to be versioned along with the prompt text.

**Decision.** Prompts follow the pattern in Anthropic's classification cookbook (platform.claude.com/cookbook/capabilities-classification-guide). That guide is the reference for classification work in this project.
- **Categories:** each label is a `<category>` with a `<label>` and a `<content>` inside `<categories>`. Each `<content>` is the labeling guide's definition, includes, and excludes, written as prose, with the decided edge cases folded in (Habitat affiliates, group homes, homeowners' associations, emergency funds).
- **Embedded in each version:** the categories are written into each prompt file, not kept in a separate file. Changing a definition means a new prompt version, so the cache key (EIN, prompt version, model) always identifies the definitions used.
- **Input:** the organization's name, mission, Part I activity line, programs with expenses, and mission/program Schedule O notes, each in its own tag. These are the same fields as on the hand-labeling sheet (D-015), so Claude and the labelers work from the same evidence.
- **Rules:** the guide's decision rules come after the input: one primary label, follow the money, housing must be primary, read Schedule O when the mission is a bare pointer (D-013), and use only the given text.
- **Output by tool use:** the call offers one strict tool, `record_label`, and forces it (`tool_choice` names it; Claude Haiku 5.5 allows forced tool use). Its schema is `reason` (a string) then `label` (an enum of `config.yaml: housing_labels`), both required, no other fields. The prompt asks for the reason first, so the model states its evidence before choosing. The parser still checks the label against the taxonomy, a missing reason, a missing tool call, and refusals; anything that fails is logged with a status, never coerced.
- **v001 is zero-shot.** Retrieved examples and a longer scratchpad (the cookbook's RAG and chain-of-thought variants) are left for later versions, each scored on dev against v001. `temperature` is not set; Haiku 5.5 doesn't accept it. A forced tool call on Haiku 5.5 skips thinking, so the reason field is the only reasoning step.

**Alternatives.**
- A separate categories file shared by prompt versions: one source for the definitions, but a definition change without a new prompt version would silently mix definitions under one cache key.
- Few-shot examples from the dev labels: prompt work is scored on dev, so its own examples would inflate the score. Examples have to come from outside the labeled sample.
- The cookbook's tag output (`<category>` with a `</category>` stop sequence): it follows the guide exactly, but the label is free text that has to be parsed. A strict tool schema with an enum guarantees well-formed output, and CLAUDE.md asks for structured output.
- Structured outputs (`output_config.format`): would also work; tool use was asked for and keeps the label and reason in one typed block.
- `tool_choice: auto` so the model can think first: no guarantee of a tool call, and thinking costs more for a six-way label.

**Consequences.** The prompt and the labeling guide now state the same definitions twice. When the D-008 review changes a label, both need updating: the guide edited, and the prompt as a new version. The reason text gives the error analysis something to read. The scores from v001 are the baseline that later versions have to beat.

## D-017: Taxonomy review after the dev labels

**Status:** Accepted
**Date:** 2026-11-23

**Context.** D-008 called for a review of the taxonomy after the first 25 hand labels. All 40 dev organizations are now labeled under the labeling guide's rules. The review asked two questions: do the six labels and their definitions hold up, and what does a strict primary-purpose taxonomy leave out?

**Decision.** Keep the six D-008 labels and their definitions unchanged, and accept D-008.
- **The labels hold up.** Under the guide's strict rules, every dev organization fell into one of the six labels, `not_housing` included. No definition needed changing.
- **They are strict by design.** A housing label goes only to an organization whose primary purpose, by program spending and then mission, is one of the five housing categories. That is how the labels should be explained: precise about organizations whose main work is housing, and blind to organizations doing real housing work that isn't their primary purpose.
- **Widening the taxonomy is a client decision, not a labeling one.** For a foundation or funder trying to understand housing in an area, the organizations a strict taxonomy misses may be worth knowing about. Whether to catch them, at the cost of a less precise label set, depends on the client's goals. It should be discussed with the client as a tradeoff, not settled here.

**Alternatives.**
- A broader label, or a `housing_related` flag, for organizations with real housing impact outside their primary purpose. Housing advocacy came up while labeling (coalitions, research, convening, promoting development). Deferred until a client says what they need; adding it now would change the hand labels' meaning after 40 are done.
- Multi-label classification (deferred in D-008). It would capture organizations doing housing alongside other work, but it is harder to label and evaluate. Still deferred, for the same reason.

**Consequences.**
- The labels can miss organizations that matter to housing in a place, for two reasons:
  - Nonprofits often serve the needs in front of them rather than their stated mission. Rule 2 (follow the money) catches some of this, but only what the filing reports.
  - The 990 data is imperfect and depends entirely on what each filer wrote. Thin or missing mission and program text limits both the hand labels and Claude's.
- Results should be reported as accuracy on this strict definition, not as a full count of organizations working on housing. Stage 2 briefs should say the same, next to the headquarters-address caveat (D-006).
- `TAXONOMY:` notes in the labeling sheets remain the place to record housing-relevant organizations that fall outside the labels, so the client discussion can start from real examples.
- The labeling guide's open questions are settled the same way: each is labeled by the closest strict rule, and broader coverage is part of the client discussion.

## D-018: Mapping NTEE codes onto the taxonomy

**Status:** Accepted
**Date:** 2026-11-23

**Context.** D-004 makes NTEE codes the baseline Claude has to beat, which means mapping each NTEE code onto one of the six labels. NTEE and the taxonomy don't line up: some NTEE codes split across several labels, some cover services that aren't housing, and many group L codes say "housing" without saying which kind.

**Decision.** `evals/ntee_taxonomy_map.csv` maps NTEE code prefixes to labels, with a reason for each row. The longest matching prefix wins.
- **Group L:**
  - L20, L21, L22 (senior housing), L25 (rehab): `affordable_housing_development`
  - L23 (housing for people with disabilities): `supportive_housing`
  - L4 (L40 temporary housing, L41 shelters, and any other L4x): `emergency_shelter`
  - L80, L81 (repairs): `homeownership`
  - L30, L82: `rental_assistance`
  - L50 (owners' and renters' associations): `not_housing`
- **Adjacent codes (D-009):** P70, P73, P75: `supportive_housing`. P85: `emergency_shelter`. P60 and the S codes: `not_housing`.
- **Vague group L codes** (L01–L19 support and advocacy, L99, anything else under L) predict `unmapped`, which is always scored wrong and gets its own column in the confusion matrix. NTEE does say these are housing organizations, but not which kind, so it gets no credit for a guess.
- **Anything else:** codes outside the map, and organizations with no NTEE code, predict `not_housing`, because NTEE doesn't call them housing.
- The mapping follows the labeling guide's rules and NTEE's code definitions. It was not fitted to the hand labels. The eval saves a hash of the map file with every result.

**Alternatives.**
- Map the vague L codes to `not_housing`: it scores NTEE more generously when the hand label is `not_housing`, but it hides that NTEE called them housing.
- Map them to the most common housing label: it makes NTEE look like it knows something it doesn't.
- Score NTEE only on housing versus not housing: a fairer test of NTEE, but not comparable to Claude's six-way label. It could be added as a second view later.

**Consequences.** Three rows are ambiguous and will cost NTEE points in predictable ways:
- L80 ("housing support") could be any housing label.
- P70 includes adult day programs, which aren't housing.
- P85 includes day centers with no beds.

The error analysis should check these rows before blaming the mapping or NTEE. A change to the map is a new decision entry, and results made with the old map stay as they are, identified by its hash.
