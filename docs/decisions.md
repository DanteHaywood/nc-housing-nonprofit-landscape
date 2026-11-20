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
