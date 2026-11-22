import re
from pathlib import Path
from typing import Any

import duckdb
import pytest

from src.ingest.build import (
    LOAD_ORDER,
    SCHEMA_SQL,
    build,
    database_path,
    load_database,
)
from src.ingest.parse import (
    FILING_COLUMNS,
    PROGRAM_COLUMNS,
    REVENUE_LINE_COLUMNS,
    SCHEDULE_O_COLUMNS,
)
from tests.conftest import (
    NOT_IN_ZIP,
    PF,
    POINTER_990EZ,
    REAL_990,
    REAL_990EZ,
    STRAY,
    make_project,
)

# The miniature project is described in tests/conftest.py.


def query(config: dict[str, Any], sql: str) -> list[tuple]:
    with duckdb.connect(str(database_path(config)), read_only=True) as con:
        return con.execute(sql).fetchall()


def count(config: dict[str, Any], table: str) -> int:
    return query(config, f"SELECT count(*) FROM ingest.{table}")[0][0]


# Every table is loaded with the expected number of rows.
def test_row_counts(built_config: dict[str, Any]) -> None:
    counts = {table: count(built_config, table) for table in LOAD_ORDER}
    assert counts == {
        "organizations": 7,  # the full BMF, not just candidates
        "state_index": 6,  # every org's index filings, 990T dropped, deduped
        "filing_index": 5,  # the manifest, after 990T and dedupe
        "filings": 3,
        "programs": 10,
        "revenue_lines": 5,
        "schedule_o": 27,
        "funnel": 16,
    }


# Each manifest filing gets the step it stopped at, and the zip it was found in.
def test_filing_index_status(built_config: dict[str, Any]) -> None:
    rows = query(
        built_config,
        "SELECT object_id, status, zip_name FROM ingest.filing_index ORDER BY 1",
    )
    assert {oid: (status, zip_name) for oid, status, zip_name in rows} == {
        NOT_IN_ZIP: ("not_in_zip", None),
        PF: ("unsupported_form", "2025_TEOS_XML_01A"),
        POINTER_990EZ: ("parsed", "2025_TEOS_XML_01A"),
        REAL_990: ("parsed", "2025_TEOS_XML_01A"),
        REAL_990EZ: ("parsed", "2025_TEOS_XML_01A"),
    }


# The funnel records each pipeline step in order.
def test_funnel(built_config: dict[str, Any]) -> None:
    rows = query(built_config, "SELECT stage, count FROM ingest.funnel ORDER BY step")
    assert rows == [
        ("BMF organizations", 7),
        ("candidates: NTEE L", 3),
        ("candidates: adjacent NTEE", 2),
        ("eval sample EINs outside the candidates", 0),
        ("index rows, 2024, 2025", 8),
        ("state filings in the index (no 990T, deduped)", 6),
        ("index rows matching a selected EIN", 7),
        ("selected EINs with an index row", 5),
        ("rows after dropping 990T", 6),
        ("filings after dedupe (manifest)", 5),
        ("located in a zip", 4),
        ("XML on disk", 4),
        ("parsed: 990", 1),
        ("parsed: 990EZ", 2),
        ("skipped: unsupported form", 1),
        ("failed to parse", 0),
    ]


# XML left in the folder by an older manifest is never loaded.
def test_stray_xml_is_not_loaded(built_config: dict[str, Any]) -> None:
    stray_file = Path(built_config["paths"]["interim"]) / "xml" / f"{STRAY}_public.xml"
    assert stray_file.is_file()
    assert query(
        built_config, f"SELECT count(*) FROM ingest.filings WHERE object_id = '{STRAY}'"
    ) == [(0,)]


# Organizations: blanks become NULL, non-candidates have no group, EINs are text.
def test_organizations(built_config: dict[str, Any]) -> None:
    rows = query(
        built_config,
        """SELECT ein, zip5, ntee_code, ntee_major_group, candidate_group,
                  bmf_asset_amt, typeof(ein)
           FROM ingest.organizations WHERE ein IN ('222222222', '333333333')
           ORDER BY ein""",
    )
    assert rows == [
        ("222222222", "27701", "L20", "L", "ntee_L", None, "VARCHAR"),
        ("333333333", "28801", None, None, None, 10, "VARCHAR"),
    ]


# Filings carry the index's tax period; amounts are BIGINT and NULL when absent.
def test_filings(built_config: dict[str, Any]) -> None:
    rows = query(
        built_config,
        f"""SELECT tax_period, return_type, total_revenue, typeof(total_revenue)
            FROM ingest.filings WHERE object_id IN ('{REAL_990}', '{POINTER_990EZ}')
            ORDER BY return_type""",
    )
    assert rows == [
        ("202412", "990", 714067, "BIGINT"),
        ("202412", "990EZ", None, "BIGINT"),
    ]


# latest_filings has exactly one row per EIN with a parsed filing.
def test_latest_filings(built_config: dict[str, Any]) -> None:
    assert query(
        built_config, "SELECT count(*), count(DISTINCT ein) FROM ingest.latest_filings"
    ) == [(3, 3)]


# schema.sql has exactly the columns parse.py produces (plus tax_period on
# filings), so the two can't drift apart silently.
@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("filings", [*FILING_COLUMNS, "tax_period"]),
        ("programs", PROGRAM_COLUMNS),
        ("revenue_lines", REVENUE_LINE_COLUMNS),
        ("schedule_o", SCHEDULE_O_COLUMNS),
    ],
)
def test_schema_matches_parse_columns(
    built_config: dict[str, Any], table: str, columns: list[str]
) -> None:
    described = query(built_config, f"DESCRIBE ingest.{table}")
    assert {row[0] for row in described} == set(columns)


# schema.sql creates every table the build loads.
def test_schema_sql_defines_every_loaded_table() -> None:
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    created = set(re.findall(r"CREATE TABLE ingest\.(\w+)", ddl))
    assert created == set(LOAD_ORDER)


# A rebuild replaces `ingest` but leaves other schemas (e.g. classify) alone.
def test_rebuild_keeps_other_schemas(tmp_path: Path) -> None:
    config = make_project(tmp_path)
    build(config)
    with duckdb.connect(str(database_path(config))) as con:
        con.execute("CREATE SCHEMA classify")
        con.execute(
            "CREATE TABLE classify.classifications (ein VARCHAR, label VARCHAR)"
        )
        con.execute("INSERT INTO classify.classifications VALUES ('474942066', 'x')")
    build(config)
    assert query(config, "SELECT * FROM classify.classifications") == [
        ("474942066", "x")
    ]
    assert count(config, "filings") == 3


# A failed load (here, a duplicate primary key) rolls back: the previous
# tables are still there, unchanged.
def test_failed_load_rolls_back(tmp_path: Path) -> None:
    config = make_project(tmp_path)
    tables = build(config).tables
    broken = dict(tables)
    broken["programs"] = tables["programs"].iloc[[0, 0]]  # same PK twice
    with pytest.raises(duckdb.ConstraintException):
        load_database(database_path(config), broken)
    assert count(config, "programs") == 10
    assert count(config, "filings") == 3
