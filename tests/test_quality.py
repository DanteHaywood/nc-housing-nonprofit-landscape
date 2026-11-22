from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from src.ingest.build import database_path
from src.ingest.quality import connect, funnel, missing_shares

# The database comes from the miniature project in tests/conftest.py: one real
# 990, one real 990-EZ, and a synthetic 990-EZ whose mission is only "SEE
# SCHEDULE O", with no total revenue and no programs.


@pytest.fixture
def con(built_config: dict[str, Any]) -> duckdb.DuckDBPyConnection:
    with connect(database_path(built_config)) as con:
        yield con


def share(shares: pd.DataFrame, scope: str, field: str) -> tuple[int, int, float]:
    row = shares.loc[(shares["scope"] == scope) & (shares["field"] == field)]
    assert len(row) == 1, (scope, field)
    return (
        int(row["missing"].iloc[0]),
        int(row["total"].iloc[0]),
        row["pct_missing"].iloc[0],
    )


# The funnel is the recorded build steps plus two organization-level steps.
# The synthetic filing's bare pointer doesn't count as mission text.
def test_funnel_appends_organization_steps(con: duckdb.DuckDBPyConnection) -> None:
    steps = funnel(con)
    assert steps["step"].tolist() == list(range(1, 17))
    assert steps.iloc[0].tolist() == [1, "BMF organizations", 6]
    assert steps.iloc[-2:]["count"].tolist() == [3, 2]
    assert steps.iloc[-2]["stage"] == "organizations with a parsed filing"


# Shares are computed per form, over that form's filings.
def test_missing_shares_per_form(con: duckdb.DuckDBPyConnection) -> None:
    shares = missing_shares(con)
    assert share(shares, "990EZ", "total_revenue") == (1, 2, 50.0)
    assert share(shares, "990EZ", "program with a description") == (1, 2, 50.0)
    assert share(shares, "990EZ", "mission is only a Schedule O pointer") == (
        1,
        2,
        50.0,
    )
    assert share(shares, "990", "total_revenue") == (0, 1, 0.0)
    assert share(shares, "990", "government_grants") == (0, 1, 0.0)


# A field a form doesn't have is not reported as missing for that form.
def test_missing_shares_skip_fields_a_form_lacks(
    con: duckdb.DuckDBPyConnection,
) -> None:
    shares = missing_shares(con)
    ez_fields = set(shares.loc[shares["scope"] == "990EZ", "field"])
    assert "government_grants" not in ez_fields
    assert "activity_or_mission" not in ez_fields
    assert share(shares, "990", "activity_or_mission") == (0, 1, 0.0)


# BMF fields are checked for the organizations that have a parsed filing.
def test_missing_shares_bmf_fields(con: duckdb.DuckDBPyConnection) -> None:
    shares = missing_shares(con)
    assert share(shares, "BMF, orgs with a filing", "ntee_code") == (0, 3, 0.0)
    assert share(shares, "BMF, orgs with a filing", "zip5") == (0, 3, 0.0)


# The report opens the database read-only, and says so if it isn't built.
def test_connect(tmp_path: Path, built_config: dict[str, Any]) -> None:
    with pytest.raises(FileNotFoundError, match="src.ingest.build"):
        connect(tmp_path / "missing.duckdb")
    with (
        connect(database_path(built_config)) as con,
        pytest.raises(duckdb.InvalidInputException),
    ):
        con.execute("CREATE TABLE ingest.scratch (x INTEGER)")
