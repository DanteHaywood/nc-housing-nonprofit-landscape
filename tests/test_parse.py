import logging
from pathlib import Path

import duckdb
import pytest

from src.ingest.parse import (
    FILING_COLUMNS,
    PROGRAM_COLUMNS,
    REVENUE_LINE_COLUMNS,
    UnsupportedForm,
    normalize_ein,
    parse_amount,
    parse_filing,
    parse_filings,
    write_parquet,
)

FIXTURES = Path(__file__).parent / "fixtures" / "xml"
# Real filings copied from data/interim/xml, both NTEE L40 (NC):
# a 990 for Tiny House Community Development Inc (tax year 2024), with programs
# on lines 4a-4c plus four "other" programs and five revenue lines,
# and a 990-EZ for Lifes Good Stay Here (tax year 2024), with three programs
# and several Part I lines left blank.
FORM_990 = FIXTURES / "202522749349301342_public.xml"
FORM_990EZ = FIXTURES / "202523219349202597_public.xml"

NS = 'xmlns="http://www.irs.gov/efile"'


def write_xml(
    tmp_path: Path, body: str, return_type: str = "990", ns: str = NS
) -> Path:
    """Write a minimal return with `body` inside ReturnData, named like the IRS."""
    path = tmp_path / "202599999999999999_public.xml"
    path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<Return {ns}>
  <ReturnHeader>
    <TaxYr>2024</TaxYr>
    <ReturnTypeCd>{return_type}</ReturnTypeCd>
    <Filer><EIN>12345678</EIN>
      <BusinessName><BusinessNameLine1Txt>TEST ORG</BusinessNameLine1Txt>
      </BusinessName></Filer>
  </ReturnHeader>
  <ReturnData>{body}</ReturnData>
</Return>""",
        encoding="utf-8",
    )
    return path


# --- Real 990 ---------------------------------------------------------------


# Header fields: EIN stays a 9-digit string, tax year is an int.
def test_990_identifiers() -> None:
    filing = parse_filing(FORM_990).filing
    assert filing["object_id"] == "202522749349301342"
    assert filing["return_type"] == "990"
    assert filing["ein"] == "474942066"
    assert filing["name"] == "Tiny House Community Development Inc"
    assert filing["tax_year"] == 2024


# Financials match the XML. Net assets reported as 0 stay 0, not None.
def test_990_financials() -> None:
    filing = parse_filing(FORM_990).filing
    assert filing["mission"].startswith(
        "to be a coalition of diverse individuals and local organizations"
    )
    expected = {
        "contributions_total": 426197,
        "government_grants": 296496,
        "other_contributions": 78735,
        "program_service_revenue": 73965,
        "total_revenue": 714067,
        "prior_year_total_revenue": 457889,
        "program_service_expenses": 335259,
        "total_expenses": 602086,
        "net_assets_boy": 0,
        "net_assets_eoy": 0,
    }
    assert {k: filing[k] for k in expected} == expected
    assert all(type(v) is int for v in expected.values())


# Columns that only exist on the 990-EZ are None on a 990.
def test_990_has_no_ez_only_fields() -> None:
    filing = parse_filing(FORM_990).filing
    for column in ("membership_dues", "salaries", "total_assets_eoy"):
        assert filing[column] is None


# Seven programs in Part III order: 4a, 4b, 4c, then four "other" (4d) programs.
def test_990_programs() -> None:
    programs = parse_filing(FORM_990).programs
    assert [(p["program_number"], p["part_iii_line"]) for p in programs] == [
        (1, "4a"),
        (2, "4b"),
        (3, "4c"),
        (4, "4d"),
        (5, "4d"),
        (6, "4d"),
        (7, "4d"),
    ]
    assert [p["description"] for p in programs] == [
        "Smith Street Construction",
        "Causey Street Development",
        "Hay Street Development",
        "Chicken Coop",
        "Freeman Mill Rd",
        "Hope Center",
        "High Point CAV Building",
    ]
    expenses = [194845, 14137, 15979, 28280, 59999, 19647, 2372]
    assert [p["expense_amt"] for p in programs] == expenses
    # This filer reported no program revenue or grants, so those stay None.
    assert all(p["revenue_amt"] is None and p["grant_amt"] is None for p in programs)
    assert all(p["ein"] == "474942066" for p in programs)


# Part VIII line 2 revenue lines, one row each, in filing order.
def test_990_revenue_lines() -> None:
    lines = parse_filing(FORM_990).revenue_lines
    assert [(r["line_number"], r["description"], r["amount"]) for r in lines] == [
        (1, "Instructional Services", 9189),
        (2, "Recycling", 57910),
        (3, "RefundsAllowancesDiscou", 4371),
        (4, "ShippingDelivery", 1817),
        (5, "Other", 678),
    ]


# --- Real 990-EZ ------------------------------------------------------------


# 990-EZ fields come from their own tags (PrimaryExemptPurposeTxt, Part I lines).
def test_990ez_identifiers_and_financials() -> None:
    filing = parse_filing(FORM_990EZ).filing
    assert filing["return_type"] == "990EZ"
    assert filing["ein"] == "920731806"
    assert filing["name"] == "LIFES GOOD STAY HERE"
    assert filing["mission"].startswith("OUR MISSION IS TO CONNECT RESOURCES")
    expected = {
        "contributions_total": 45502,
        "program_service_revenue": 134214,
        "total_revenue": 179716,
        "gross_receipts": 179716,
        "program_service_expenses": 149439,
        "salaries": 6105,
        "contractor_fees": 88756,
        "other_expenses": 68605,
        "total_expenses": 163466,
        "excess_or_deficit": 16250,
        "net_assets_boy": 0,
        "net_assets_eoy": 16250,
        "total_assets_boy": 0,
        "total_assets_eoy": 16338,
    }
    assert {k: filing[k] for k in expected} == expected


# Lines this filer left blank come back as None, and the filing logs them.
def test_990ez_missing_fields_are_none_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="src.ingest.parse"):
        filing = parse_filing(FORM_990EZ).filing
    blank = [
        "membership_dues",
        "investment_income",
        "inventory_gross_profit",
        "other_revenue",
        "grants_paid",
        "occupancy",
    ]
    assert all(filing[c] is None for c in blank)
    for column in blank:
        assert column in caplog.text
    # 990-only columns are None but not "missing" for a 990-EZ.
    assert filing["government_grants"] is None
    assert "government_grants" not in caplog.text


# Three uniform program groups, no Part III line letters, no revenue lines.
def test_990ez_programs() -> None:
    parsed = parse_filing(FORM_990EZ)
    programs = parsed.programs
    assert [p["program_number"] for p in programs] == [1, 2, 3]
    assert all(p["part_iii_line"] is None for p in programs)
    assert [p["expense_amt"] for p in programs] == [58929, 35830, 54680]
    assert programs[0]["description"].startswith("1.ALTERNATIVE FAMILY LIVING")
    assert all(p["revenue_amt"] is None for p in programs)
    assert parsed.revenue_lines == []


# Program descriptions have their whitespace collapsed (no newlines or runs).
def test_text_is_whitespace_normalized() -> None:
    for path in (FORM_990, FORM_990EZ):
        parsed = parse_filing(path)
        texts = [parsed.filing["mission"]] + [p["description"] for p in parsed.programs]
        assert all("\n" not in t and "  " not in t for t in texts)


# --- Edge cases -------------------------------------------------------------


# Amounts parse to int. Whole-dollar decimals are accepted, anything else is None.
@pytest.mark.parametrize(
    ("text", "expected"),
    [("12345", 12345), ("-500", -500), ("0", 0), ("100.00", 100), ("12.50", None)],
)
def test_parse_amount(text: str, expected: int | None) -> None:
    assert parse_amount(text) == expected


# EINs are zero-padded; non-digit EINs become None instead of raising.
def test_normalize_ein() -> None:
    assert normalize_ein("12345678", "x") == "012345678"
    assert normalize_ein("12-345678", "x") is None
    assert normalize_ein(None, "x") is None


# A file without the IRS namespace parses the same way.
def test_parses_without_namespace(tmp_path: Path) -> None:
    body = "<IRS990><MissionDesc>Homes</MissionDesc><TotalContributionsAmt>5"
    body += "</TotalContributionsAmt></IRS990>"
    filing = parse_filing(write_xml(tmp_path, body, ns="")).filing
    assert filing["ein"] == "012345678"
    assert filing["mission"] == "Homes"
    assert filing["contributions_total"] == 5


# A non-whole-dollar amount becomes None with a warning; parsing carries on.
def test_bad_amount_is_none_and_warned(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    body = "<IRS990><TotalContributionsAmt>12.50</TotalContributionsAmt>"
    body += "<PYTotalRevenueAmt>900</PYTotalRevenueAmt></IRS990>"
    with caplog.at_level(logging.WARNING, logger="src.ingest.parse"):
        filing = parse_filing(write_xml(tmp_path, body)).filing
    assert filing["contributions_total"] is None
    assert filing["prior_year_total_revenue"] == 900
    assert "not a whole-dollar amount" in caplog.text


# A filing with an empty form element still yields a row, all fields None.
def test_empty_form_yields_row_of_nones(tmp_path: Path) -> None:
    parsed = parse_filing(write_xml(tmp_path, "<IRS990/>"))
    assert parsed.filing["ein"] == "012345678"
    assert parsed.filing["mission"] is None
    assert parsed.filing["total_revenue"] is None
    assert parsed.programs == []


# A return type with its form element missing entirely still yields a row.
def test_missing_form_element_yields_row(tmp_path: Path) -> None:
    filing = parse_filing(write_xml(tmp_path, "", return_type="990EZ")).filing
    assert filing["return_type"] == "990EZ"
    assert filing["total_revenue"] is None


# 990-PF isn't in the field map, so it raises UnsupportedForm (D-012).
def test_990pf_is_unsupported(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedForm) as raised:
        parse_filing(write_xml(tmp_path, "<IRS990PF/>", return_type="990PF"))
    assert raised.value.return_type == "990PF"


# A batch run counts skipped forms and unreadable files instead of stopping.
def test_parse_filings_skips_and_counts(tmp_path: Path) -> None:
    pf = write_xml(tmp_path, "<IRS990PF/>", return_type="990PF")
    broken = tmp_path / "202588888888888888_public.xml"
    broken.write_text("<Return><ReturnHeader>", encoding="utf-8")
    result = parse_filings([FORM_990, FORM_990EZ, pf, broken])
    assert result.skipped == {"990PF": 1}
    assert result.failed == [broken.name]
    assert result.filings["return_type"].tolist() == ["990", "990EZ"]
    assert len(result.programs) == 10
    assert len(result.revenue_lines) == 5


# Tables have fixed columns; amounts are nullable Int64 and EINs stay strings.
def test_parse_filings_columns_and_dtypes() -> None:
    result = parse_filings([FORM_990, FORM_990EZ])
    assert list(result.filings.columns) == FILING_COLUMNS
    assert list(result.programs.columns) == PROGRAM_COLUMNS
    assert list(result.revenue_lines.columns) == REVENUE_LINE_COLUMNS
    assert str(result.filings["total_revenue"].dtype) == "Int64"
    assert str(result.programs["grant_amt"].dtype) == "Int64"
    assert result.filings["ein"].map(type).eq(str).all()


# Parquet keeps amounts as BIGINT (with NULLs) and EINs as VARCHAR.
def test_write_parquet_types(tmp_path: Path) -> None:
    path = tmp_path / "filings.parquet"
    write_parquet(parse_filings([FORM_990, FORM_990EZ]).filings, path)
    source = f"read_parquet('{path.as_posix()}')"
    described = duckdb.sql(f"DESCRIBE SELECT * FROM {source}").fetchall()
    types = {row[0]: row[1] for row in described}
    assert types["ein"] == "VARCHAR"
    assert types["total_revenue"] == "BIGINT"
    nulls = duckdb.sql(f"SELECT count(*) FROM {source} WHERE membership_dues IS NULL")
    assert nulls.fetchall() == [(2,)]
