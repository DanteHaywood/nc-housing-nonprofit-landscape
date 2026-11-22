"""Report on the built database: the ingest funnel and missing key fields.

Opens `paths.processed` read-only and prints:
1. the funnel: row counts at each pipeline step, from the BMF to organizations
   whose latest filing has usable mission text, and
2. the share of filings missing each key field, by form, for the fields that
   form has.

All arithmetic happens in SQL. NULL means "not reported", and the IRS often
leaves a field out when it is zero, so a missing share is not a share of
zeros (D-012).

Run with `python -m src.ingest.quality` after `python -m src.ingest.build`.
"""

from pathlib import Path

import duckdb
import pandas as pd

from src.config import load_config
from src.ingest.build import database_path
from src.ingest.parse import FORM_990, FORM_990EZ, FORM_FIELDS

# A mission that is only a pointer to Schedule O, e.g. "SEE SCHEDULE O" (D-013).
BARE_SCHEDULE_O = r"^\W*(see|refer to)?\s*(attached\s+)?sched(ule)?\.?\s*o\W*$"

# Filing fields worth checking. Each is checked only for the forms that have it.
KEY_FILING_FIELDS = [
    "mission",
    "activity_or_mission",
    "contributions_total",
    "government_grants",
    "program_service_revenue",
    "total_revenue",
    "program_service_expenses",
    "total_expenses",
    "net_assets_eoy",
]
KEY_BMF_FIELDS = ["zip5", "ntee_code"]


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    """Open the database read-only. Raises FileNotFoundError if it isn't built."""
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found; run python -m src.ingest.build")
    return duckdb.connect(str(path), read_only=True)


def funnel(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return (step, stage, count) from the BMF to orgs with usable mission text.

    Steps 1..n come from `ingest.funnel`, recorded at build time. The last two
    are computed from the tables: organizations with a parsed filing, and those
    whose latest filing has a mission that isn't just "See Schedule O".
    """
    return con.execute(
        """
        WITH recorded AS (SELECT step, stage, count FROM ingest.funnel),
        last AS (SELECT max(step) AS step FROM recorded)
        SELECT * FROM recorded
        UNION ALL
        SELECT (SELECT step FROM last) + 1,
               'organizations with a parsed filing',
               count(DISTINCT ein)
        FROM ingest.filings
        UNION ALL
        SELECT (SELECT step FROM last) + 2,
               '  latest filing has mission text (not a bare Schedule O pointer)',
               count(*)
        FROM ingest.latest_filings
        WHERE mission IS NOT NULL
          AND NOT regexp_matches(mission, ?, 'i')
        ORDER BY 1
        """,
        [BARE_SCHEDULE_O],
    ).df()


def _missing_sql(field: str, forms: list[str]) -> str:
    """One SELECT counting NULLs of `field` per form in `forms`."""
    form_list = ", ".join(f"'{form}'" for form in forms)
    return f"""
        SELECT return_type AS scope, '{field}' AS field,
               count(*) FILTER (WHERE {field} IS NULL) AS missing,
               count(*) AS total
        FROM ingest.filings WHERE return_type IN ({form_list})
        GROUP BY return_type"""


def missing_shares(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Return the share of rows missing each key field, by form.

    Columns: scope (990, 990EZ, or a BMF scope), field, missing, total, and
    pct_missing. Filing fields are checked only for forms that have them (from
    `parse.FORM_FIELDS`). Also counts filings with no described program, missions
    that are only a Schedule O pointer, and BMF fields for organizations with a
    parsed filing.
    """
    parts = [
        _missing_sql(
            field, [f for f in (FORM_990, FORM_990EZ) if f in FORM_FIELDS[field]]
        )
        for field in KEY_FILING_FIELDS
    ]
    parts.append(
        """
        SELECT f.return_type, 'program with a description',
               count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM ingest.programs p
                   WHERE p.object_id = f.object_id AND p.description IS NOT NULL)),
               count(*)
        FROM ingest.filings f GROUP BY f.return_type"""
    )
    parts.append(
        """
        SELECT return_type, 'mission is only a Schedule O pointer',
               count(*) FILTER (WHERE regexp_matches(mission, $pointer, 'i')),
               count(*)
        FROM ingest.filings GROUP BY return_type"""
    )
    for field in KEY_BMF_FIELDS:
        parts.append(
            f"""
            SELECT 'BMF, orgs with a filing', '{field}',
                   count(*) FILTER (WHERE {field} IS NULL), count(*)
            FROM ingest.organizations
            WHERE ein IN (SELECT ein FROM ingest.filings)"""
        )
    union = "\nUNION ALL".join(parts)
    return con.execute(
        f"""
        SELECT scope, field, missing, total,
               round(100.0 * missing / total, 1) AS pct_missing
        FROM ({union})
        ORDER BY scope, field
        """,
        {"pointer": BARE_SCHEDULE_O},
    ).df()


def main() -> None:
    """Print the funnel and missing-field shares for the database in config.yaml."""
    path = database_path(load_config())
    with connect(path) as con:
        built_at = con.execute("SELECT max(built_at) FROM ingest.funnel").fetchone()
        print(f"{path}, built {built_at[0]:%Y-%m-%d %H:%M}\n")
        print("Funnel")
        steps = funnel(con)
        width = steps["stage"].str.len().max()
        for row in steps.itertuples():
            print(f"  {row.step:>2}. {row.stage:<{width}}  {row.count:>8,}")

        print("\nMissing key fields (NULL = not reported; often means zero)")
        shares = missing_shares(con)
        for scope, group in shares.groupby("scope", sort=False):
            print(f"  {scope}")
            for row in group.itertuples():
                print(
                    f"    {row.field:<40}{row.pct_missing:>6.1f}%"
                    f"  ({row.missing:,} of {row.total:,})"
                )


if __name__ == "__main__":
    main()
