-- Schema `ingest`: everything src/ingest/build.py loads (D-014).
-- The build drops and recreates this whole schema inside one transaction, so a
-- failed build leaves the previous tables in place. Other schemas (`classify`,
-- `agent`) are never touched by the build, and nothing here references them.
--
-- Conventions: EINs are 9-character strings, never integers. Dollar amounts are
-- whole dollars (BIGINT). NULL means "not reported", which on IRS forms often
-- means zero (D-012). Column docs: docs/schema.md.

CREATE SCHEMA ingest;

-- One row per organization in the state BMF, candidates or not. The NTEE
-- baseline for the eval, the frame for its random sample (D-009), and the
-- agent's directory, including organizations with no e-filed return.
CREATE TABLE ingest.organizations (
    ein               VARCHAR PRIMARY KEY CHECK (length(ein) = 9),
    name              VARCHAR NOT NULL,
    street            VARCHAR,
    city              VARCHAR,
    state             VARCHAR NOT NULL,
    zip               VARCHAR,           -- as in the BMF, e.g. 28401-1234
    zip5              VARCHAR,           -- first five digits, for geography joins
    ntee_code         VARCHAR,           -- e.g. L20Z; NULL if the BMF has none
    ntee_major_group  VARCHAR,           -- first letter of ntee_code
    candidate_group   VARCHAR CHECK (candidate_group IN ('ntee_L', 'ntee_adjacent')),
    subsection        VARCHAR,           -- 501(c) subsection, e.g. 03
    foundation_code   VARCHAR,
    ruling            VARCHAR,           -- YYYYMM the exemption was granted
    bmf_tax_period    VARCHAR,           -- YYYYMM of the return the BMF figures use
    bmf_asset_amt     BIGINT,
    bmf_income_amt    BIGINT,
    bmf_revenue_amt   BIGINT
);

-- One row per filing selected by the manifest (latest per EIN and tax period,
-- D-010), with what happened to it. The funnel and coverage gaps come from here.
CREATE TABLE ingest.filing_index (
    object_id      VARCHAR PRIMARY KEY CHECK (length(object_id) = 18),
    ein            VARCHAR NOT NULL REFERENCES ingest.organizations (ein),
    tax_period     VARCHAR NOT NULL,     -- YYYYMM the tax period ended
    return_type    VARCHAR NOT NULL,     -- 990, 990EZ, 990PF
    taxpayer_name  VARCHAR,
    index_year     INTEGER NOT NULL,     -- which yearly index listed it
    xml_batch_id   VARCHAR,              -- zip the index names (wrong for some, D-011)
    zip_name       VARCHAR,              -- zip it was actually found in
    status         VARCHAR NOT NULL CHECK (status IN (
        'parsed', 'unsupported_form', 'not_in_zip', 'unreadable',
        'not_extracted', 'parse_failed'
    )),
    UNIQUE (ein, tax_period)
);

-- One row per parsed 990 or 990-EZ (D-012). Several per EIN across tax periods,
-- which is what revenue trends are computed from.
CREATE TABLE ingest.filings (
    object_id                 VARCHAR PRIMARY KEY REFERENCES ingest.filing_index (object_id),
    ein                       VARCHAR NOT NULL REFERENCES ingest.organizations (ein),
    tax_period                VARCHAR NOT NULL,
    return_type               VARCHAR NOT NULL CHECK (return_type IN ('990', '990EZ')),
    tax_year                  INTEGER,   -- IRS TaxYr: the year the period began
    name                      VARCHAR,   -- name on the filing (may differ from BMF)
    mission                   VARCHAR,   -- 990 MissionDesc / 990-EZ PrimaryExemptPurposeTxt
    activity_or_mission       VARCHAR,   -- 990 Part I line 1 (D-013)
    contributions_total       BIGINT,
    government_grants         BIGINT,    -- 990 only
    other_contributions       BIGINT,    -- 990 only
    program_service_revenue   BIGINT,
    membership_dues           BIGINT,    -- 990-EZ only
    investment_income         BIGINT,    -- 990-EZ only
    inventory_gross_profit    BIGINT,    -- 990-EZ only
    other_revenue             BIGINT,    -- 990-EZ only
    total_revenue             BIGINT,
    prior_year_total_revenue  BIGINT,    -- 990 only
    gross_receipts            BIGINT,    -- 990-EZ only
    program_service_expenses  BIGINT,
    grants_paid               BIGINT,    -- 990-EZ only
    salaries                  BIGINT,    -- 990-EZ only
    contractor_fees           BIGINT,    -- 990-EZ only
    occupancy                 BIGINT,    -- 990-EZ only
    other_expenses            BIGINT,    -- 990-EZ only
    total_expenses            BIGINT,
    excess_or_deficit         BIGINT,    -- 990-EZ only
    net_assets_boy            BIGINT,
    net_assets_eoy            BIGINT,
    total_assets_boy          BIGINT,    -- 990-EZ only
    total_assets_eoy          BIGINT,    -- 990-EZ only
    UNIQUE (ein, tax_period)
);

-- One row per Part III program service accomplishment.
CREATE TABLE ingest.programs (
    object_id       VARCHAR NOT NULL REFERENCES ingest.filings (object_id),
    ein             VARCHAR NOT NULL,
    return_type     VARCHAR NOT NULL,
    program_number  INTEGER NOT NULL,    -- 1..n in filing order
    part_iii_line   VARCHAR,             -- 4a, 4b, 4c, 4d on the 990; NULL on the 990-EZ
    description     VARCHAR,
    expense_amt     BIGINT,
    grant_amt       BIGINT,
    revenue_amt     BIGINT,              -- 990 only
    PRIMARY KEY (object_id, program_number)
);

-- One row per 990 Part VIII line 2 program service revenue stream.
CREATE TABLE ingest.revenue_lines (
    object_id    VARCHAR NOT NULL REFERENCES ingest.filings (object_id),
    ein          VARCHAR NOT NULL,
    line_number  INTEGER NOT NULL,
    description  VARCHAR,
    amount       BIGINT,
    PRIMARY KEY (object_id, line_number)
);

-- One row per Schedule O supplemental note (D-013).
CREATE TABLE ingest.schedule_o (
    object_id              VARCHAR NOT NULL REFERENCES ingest.filings (object_id),
    ein                    VARCHAR NOT NULL,
    return_type            VARCHAR NOT NULL,
    entry_number           INTEGER NOT NULL,
    reference              VARCHAR,      -- the filer's free-text line reference
    explanation            VARCHAR,
    part                   VARCHAR,      -- Roman numeral read from reference, or NULL
    is_mission_or_program  BOOLEAN NOT NULL,
    PRIMARY KEY (object_id, entry_number)
);

-- Row counts at each pipeline step, recorded when the database was built.
-- Steps that leave no table behind (e.g. rows dropped as 990-T) are only here.
CREATE TABLE ingest.funnel (
    step      INTEGER PRIMARY KEY,
    stage     VARCHAR NOT NULL,
    count     BIGINT NOT NULL,
    built_at  TIMESTAMP NOT NULL
);

-- Each EIN's most recent parsed filing: the default classifier input and the
-- agent's "current size". Ties on tax_period (not expected: filing_index is
-- unique on ein + tax_period) fall to the higher OBJECT_ID.
CREATE VIEW ingest.latest_filings AS
SELECT *
FROM ingest.filings
QUALIFY row_number() OVER (
    PARTITION BY ein ORDER BY tax_period DESC, object_id DESC
) = 1;
