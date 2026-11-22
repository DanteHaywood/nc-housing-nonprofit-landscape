"""Parse IRS 990 and 990-EZ e-file XML into tables, following docs/field_map.md.

Each filing becomes:
- one row in `filings` (identifiers, mission, financials),
- one row in `programs` per program service accomplishment (Part III),
- one row in `revenue_lines` per program service revenue line (990 Part VIII,
  line 2). The 990-EZ has no equivalent.
- one row in `schedule_o` per Schedule O supplemental note, with a best-effort
  form part and a mission/program flag taken from its free-text reference (D-013).

Forms other than 990 and 990-EZ (in practice 990-PF) are skipped (D-012).
A field missing from a filing becomes None and is logged. It never raises.
The IRS often leaves a field out when its value is zero, so None means "not
reported", not "zero". Dollar amounts are ints. Text is whitespace-normalized.

Run with `python -m src.ingest.parse` after `python -m src.ingest.extract`.
"""

import logging
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from lxml import etree

from src.config import load_config
from src.ingest.extract import xml_dir, xml_path
from src.ingest.manifest import manifest_path

logger = logging.getLogger(__name__)

IRS_NAMESPACE = "http://www.irs.gov/efile"
FORM_990 = "990"
FORM_990EZ = "990EZ"
# ReturnTypeCd -> the element under ReturnData that holds the form.
FORM_ROOTS = {FORM_990: "IRS990", FORM_990EZ: "IRS990EZ"}

# Header fields, the same on every form. Paths are relative to <Return>.
HEADER_FIELDS = {
    "ein": "ReturnHeader/Filer/EIN",
    "name": "ReturnHeader/Filer/BusinessName/BusinessNameLine1Txt",
    "tax_year": "ReturnHeader/TaxYr",
}
RETURN_TYPE_PATH = "ReturnHeader/ReturnTypeCd"

# Filing-level form fields: column -> {form: path relative to the form root}.
# A column a form doesn't have (e.g. government_grants on the 990-EZ) is None
# for that form and isn't logged as missing. All of these are dollar amounts
# except `mission`.
FORM_FIELDS: dict[str, dict[str, str]] = {
    "mission": {FORM_990: "MissionDesc", FORM_990EZ: "PrimaryExemptPurposeTxt"},
    # Part I line 1, "mission or most significant activities". 990 only (D-013).
    "activity_or_mission": {FORM_990: "ActivityOrMissionDesc"},
    # Contributions and government grants
    "contributions_total": {
        FORM_990: "TotalContributionsAmt",
        FORM_990EZ: "ContributionsGiftsGrantsEtcAmt",
    },
    "government_grants": {FORM_990: "GovernmentGrantsAmt"},
    "other_contributions": {FORM_990: "AllOtherContributionsAmt"},
    # Revenue
    "program_service_revenue": {
        FORM_990: "TotalProgramServiceRevenueAmt",
        FORM_990EZ: "ProgramServiceRevenueAmt",
    },
    "membership_dues": {FORM_990EZ: "MembershipDuesAmt"},
    "investment_income": {FORM_990EZ: "InvestmentIncomeAmt"},
    "inventory_gross_profit": {FORM_990EZ: "GrossProfitLossSlsOfInvntryAmt"},
    "other_revenue": {FORM_990EZ: "OtherRevenueTotalAmt"},
    "total_revenue": {
        FORM_990: "TotalRevenueGrp/TotalRevenueColumnAmt",
        FORM_990EZ: "TotalRevenueAmt",
    },
    "prior_year_total_revenue": {FORM_990: "PYTotalRevenueAmt"},
    "gross_receipts": {FORM_990EZ: "GrossReceiptsAmt"},
    # Expenses
    "program_service_expenses": {
        FORM_990: "TotalProgramServiceExpensesAmt",
        FORM_990EZ: "TotalProgramServiceExpensesAmt",
    },
    "grants_paid": {FORM_990EZ: "GrantsAndSimilarAmountsPaidAmt"},
    # docs/field_map.md had "...EmplBnftsAmt"; the schema tag has no "s" (D-012).
    "salaries": {FORM_990EZ: "SalariesOtherCompEmplBnftAmt"},
    "contractor_fees": {FORM_990EZ: "FeesAndOtherPymtToIndCntrctAmt"},
    "occupancy": {FORM_990EZ: "OccupancyRentUtltsAndMaintAmt"},
    "other_expenses": {FORM_990EZ: "OtherExpensesTotalAmt"},
    "total_expenses": {
        FORM_990: "TotalFunctionalExpensesGrp/TotalAmt",
        FORM_990EZ: "TotalExpensesAmt",
    },
    "excess_or_deficit": {FORM_990EZ: "ExcessOrDeficitForYearAmt"},
    # Net assets and total assets
    "net_assets_boy": {
        FORM_990: "TotalNetAssetsFundBalanceGrp/BOYAmt",
        FORM_990EZ: "NetAssetsOrFundBalancesGrp/BOYAmt",
    },
    "net_assets_eoy": {
        FORM_990: "TotalNetAssetsFundBalanceGrp/EOYAmt",
        FORM_990EZ: "NetAssetsOrFundBalancesGrp/EOYAmt",
    },
    "total_assets_boy": {FORM_990EZ: "Form990TotalAssetsGrp/BOYAmt"},
    "total_assets_eoy": {FORM_990EZ: "Form990TotalAssetsGrp/EOYAmt"},
}
TEXT_COLUMNS = frozenset({"mission", "activity_or_mission"})

# Program fields: column -> {form: path relative to one program's element}.
# On the 990, program 1 (line 4a) sits directly under the form root. Programs 2
# and 3 and all "other" programs (4d) each have their own group element.
PROGRAM_FIELDS: dict[str, dict[str, str]] = {
    "description": {FORM_990: "Desc", FORM_990EZ: "DescriptionProgramSrvcAccomTxt"},
    "expense_amt": {FORM_990: "ExpenseAmt", FORM_990EZ: "ProgramServiceExpensesAmt"},
    "grant_amt": {FORM_990: "GrantAmt", FORM_990EZ: "GrantsAndAllocationsAmt"},
    "revenue_amt": {FORM_990: "RevenueAmt"},
}
PROGRAM_GROUPS_990 = [
    ("4a", None),  # fields directly under IRS990
    ("4b", "ProgSrvcAccomActy2Grp"),
    ("4c", "ProgSrvcAccomActy3Grp"),
    ("4d", "ProgSrvcAccomActyOtherGrp"),  # repeats
]
PROGRAM_GROUP_990EZ = "ProgramSrvcAccomplishmentGrp"
REVENUE_LINE_GROUP = "ProgramServiceRevenueGrp"

# Schedule O sits beside the form under ReturnData. Each note has a free-text
# reference typed by the filer ("FORM 990, PAGE 2, PART III, LINE 4D",
# "Pt VI, Line 11b", "FORM 990 - ORGANIZATION'S MISSION") and an explanation.
SCHEDULE_O_ENTRIES = "ReturnData/IRS990ScheduleO/SupplementalInformationDetail"
# "PART III", "Part 3", "Pt VI", "Pt. 6". Longest numeral first, so IX isn't read as I.
PART_PATTERN = re.compile(
    r"\b(?:PART|PT)\.?\s*(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I|\d{1,2})\b", re.I
)
ROMAN_PARTS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
# Only used when the reference names no part.
MISSION_OR_PROGRAM_PATTERN = re.compile(
    r"MISSION|PROGRAM\s+SERVICE|ACCOMPLISHMENT|EXEMPT\s+PURPOSE", re.I
)

FILING_COLUMNS = ["object_id", "return_type", *HEADER_FIELDS, *FORM_FIELDS]
PROGRAM_COLUMNS = [
    "object_id",
    "ein",
    "return_type",
    "program_number",
    "part_iii_line",
    *PROGRAM_FIELDS,
]
REVENUE_LINE_COLUMNS = ["object_id", "ein", "line_number", "description", "amount"]
SCHEDULE_O_COLUMNS = [
    "object_id",
    "ein",
    "return_type",
    "entry_number",
    "reference",
    "explanation",
    "part",
    "is_mission_or_program",
]

TABLE_NAMES = ("filings", "programs", "revenue_lines", "schedule_o")

AMOUNT_COLUMNS = {
    "filings": [c for c in FORM_FIELDS if c not in TEXT_COLUMNS],
    "programs": ["expense_amt", "grant_amt", "revenue_amt"],
    "revenue_lines": ["amount"],
}


class UnsupportedForm(Exception):
    """Raised for a return type this parser doesn't map, such as 990-PF."""

    def __init__(self, object_id: str, return_type: str | None) -> None:
        super().__init__(f"{object_id}: unsupported return type {return_type}")
        self.return_type = return_type or "unknown"


@dataclass
class ParsedFiling:
    """One filing's rows: the filing itself, its programs, its revenue lines."""

    filing: dict[str, Any]
    programs: list[dict[str, Any]] = field(default_factory=list)
    revenue_lines: list[dict[str, Any]] = field(default_factory=list)
    schedule_o: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ParseResult:
    """All parsed tables plus counts of files that produced no rows."""

    filings: pd.DataFrame
    programs: pd.DataFrame
    revenue_lines: pd.DataFrame
    schedule_o: pd.DataFrame
    skipped: dict[str, int]  # return type -> count of files skipped
    failed: list[str]  # files that couldn't be parsed as XML
    # OBJECT_IDs skipped for an unsupported form, e.g. 990-PF
    unsupported: list[str] = field(default_factory=list)
    # manifest OBJECT_IDs with no XML file (set by run_parse)
    not_on_disk: list[str] = field(default_factory=list)


class _Form:
    """Namespace-aware lookups on one parsed filing, with missing-field tracking."""

    def __init__(self, root: etree._Element, object_id: str) -> None:
        self.root = root
        self.object_id = object_id
        self.ns = etree.QName(root).namespace  # None if the file has no namespace
        self.missing: list[str] = []

    def path(self, relative: str) -> str:
        """Turn `A/B` into Clark notation (`{ns}A/{ns}B`) for this file's namespace."""
        if self.ns is None:
            return relative
        return "/".join(f"{{{self.ns}}}{step}" for step in relative.split("/"))

    def find(self, parent: etree._Element, relative: str) -> etree._Element | None:
        return parent.find(self.path(relative))

    def findall(self, parent: etree._Element, relative: str) -> list[etree._Element]:
        return parent.findall(self.path(relative))

    def text(self, parent: etree._Element, relative: str, label: str) -> str | None:
        """Return the element's whitespace-normalized text, or None (logged)."""
        matches = self.findall(parent, relative)
        if len(matches) > 1:
            logger.warning(
                "%s: %d matches for %s, using the first",
                self.object_id,
                len(matches),
                label,
            )
        value = normalize_text(matches[0].text) if matches else None
        if value is None:
            self.missing.append(label)
        return value

    def amount(self, parent: etree._Element, relative: str, label: str) -> int | None:
        """Return the element's value as whole dollars, or None (logged)."""
        raw = self.text(parent, relative, label)
        if raw is None:
            return None
        value = parse_amount(raw)
        if value is None:
            logger.warning(
                "%s: %s is not a whole-dollar amount: %r", self.object_id, label, raw
            )
        return value


def normalize_text(text: str | None) -> str | None:
    """Collapse runs of whitespace to single spaces. Blank or None becomes None."""
    if text is None:
        return None
    collapsed = " ".join(text.split())
    return collapsed or None


def parse_amount(text: str) -> int | None:
    """Parse an IRS amount like `"12345"` or `"-500"` to int.

    A value with a zero fractional part (`"100.00"`) is accepted. Anything else
    (`"12.50"`, `"N/A"`) returns None, and the caller logs it.
    """
    try:
        return int(text)
    except ValueError:
        pass
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return int(value) if value.is_finite() and value == value.to_integral() else None


def normalize_ein(raw: str | None, object_id: str) -> str | None:
    """Zero-pad an EIN to 9 digits. A malformed EIN becomes None and is logged."""
    if raw is None:
        return None
    ein = raw.zfill(9)
    if not (len(ein) == 9 and ein.isdigit()):
        logger.warning("%s: malformed EIN %r", object_id, raw)
        return None
    return ein


def object_id_from_path(path: Path) -> str:
    """Return the OBJECT_ID from a file named `<OBJECT_ID>_public.xml`."""
    return path.name.removesuffix("_public.xml")


def _parse_programs(
    form: _Form, form_root: etree._Element, return_type: str, ein: str | None
) -> list[dict[str, Any]]:
    """Return one row per program service accomplishment on this form."""
    groups: list[tuple[str | None, etree._Element]] = []
    if return_type == FORM_990:
        for line, tag in PROGRAM_GROUPS_990:
            if tag is None:
                # Line 4a has no wrapper element, so only count it if it has a field.
                if any(
                    form.find(form_root, PROGRAM_FIELDS[c][FORM_990]) is not None
                    for c in PROGRAM_FIELDS
                ):
                    groups.append((line, form_root))
            else:
                groups.extend(
                    (line, element) for element in form.findall(form_root, tag)
                )
    else:
        groups = [
            (None, element) for element in form.findall(form_root, PROGRAM_GROUP_990EZ)
        ]

    rows = []
    for number, (line, element) in enumerate(groups, start=1):
        row: dict[str, Any] = {
            "object_id": form.object_id,
            "ein": ein,
            "return_type": return_type,
            "program_number": number,
            "part_iii_line": line,
        }
        for column, paths in PROGRAM_FIELDS.items():
            relative = paths.get(return_type)
            label = f"program {number} {column}"
            if relative is None:
                row[column] = None
            elif column == "description":
                row[column] = form.text(element, relative, label)
            else:
                row[column] = form.amount(element, relative, label)
        rows.append(row)
    return rows


def _parse_revenue_lines(
    form: _Form, form_root: etree._Element, ein: str | None
) -> list[dict[str, Any]]:
    """Return one row per 990 Part VIII line 2 revenue line."""
    rows = []
    for number, element in enumerate(
        form.findall(form_root, REVENUE_LINE_GROUP), start=1
    ):
        rows.append(
            {
                "object_id": form.object_id,
                "ein": ein,
                "line_number": number,
                "description": form.text(
                    element, "Desc", f"revenue line {number} description"
                ),
                "amount": form.amount(
                    element, "TotalRevenueColumnAmt", f"revenue line {number} amount"
                ),
            }
        )
    return rows


def reference_part(reference: str | None) -> str | None:
    """Return the form part a Schedule O reference names, as a Roman numeral.

    `"FORM 990, PAGE 2, PART III, LINE 4D"` -> `"III"`, `"Pt VI, Line 11b"` ->
    `"VI"`, `"FORM 990, PART 1, LINE 1:"` -> `"I"`. Returns None when no part
    is named (`"Other Expenses.1"`) or the number is out of range.
    """
    if reference is None:
        return None
    match = PART_PATTERN.search(reference)
    if match is None:
        return None
    part = match.group(1).upper()
    if part.isdigit():
        number = int(part)
        return ROMAN_PARTS[number - 1] if 1 <= number <= len(ROMAN_PARTS) else None
    return part


def is_mission_or_program(reference: str | None, part: str | None) -> bool:
    """True if a Schedule O note is about the mission or programs (Part III).

    A note that names Part III counts. A note that names no part counts if its
    reference mentions the mission, program services, accomplishments, or
    exempt purpose (e.g. `"FORM 990 - ORGANIZATION'S MISSION"`). A note naming
    another part doesn't count, even if it mentions "program service expenses".
    """
    if part is not None:
        return part == "III"
    return bool(reference and MISSION_OR_PROGRAM_PATTERN.search(reference))


def _parse_schedule_o(
    form: _Form, root: etree._Element, return_type: str, ein: str | None
) -> list[dict[str, Any]]:
    """Return one row per Schedule O supplemental note, in filing order."""
    rows = []
    for number, element in enumerate(form.findall(root, SCHEDULE_O_ENTRIES), start=1):
        reference = form.text(
            element, "FormAndLineReferenceDesc", f"schedule O {number} reference"
        )
        part = reference_part(reference)
        rows.append(
            {
                "object_id": form.object_id,
                "ein": ein,
                "return_type": return_type,
                "entry_number": number,
                "reference": reference,
                "explanation": form.text(
                    element, "ExplanationTxt", f"schedule O {number} explanation"
                ),
                "part": part,
                "is_mission_or_program": is_mission_or_program(reference, part),
            }
        )
    return rows


def parse_root(root: etree._Element, object_id: str) -> ParsedFiling:
    """Parse an already-loaded `<Return>` element.

    Missing fields come back as None and are logged, one INFO line per filing.
    Raises UnsupportedForm if the return type isn't 990 or 990-EZ.
    """
    form = _Form(root, object_id)
    type_element = form.find(root, RETURN_TYPE_PATH)
    return_type = (
        normalize_text(type_element.text) if type_element is not None else None
    )
    if return_type not in FORM_ROOTS:
        raise UnsupportedForm(object_id, return_type)

    filing: dict[str, Any] = {"object_id": object_id, "return_type": return_type}
    filing["ein"] = normalize_ein(
        form.text(root, HEADER_FIELDS["ein"], "ein"), object_id
    )
    filing["name"] = form.text(root, HEADER_FIELDS["name"], "name")
    tax_year = form.text(root, HEADER_FIELDS["tax_year"], "tax_year")
    filing["tax_year"] = int(tax_year) if tax_year and tax_year.isdigit() else None
    if tax_year and filing["tax_year"] is None:
        logger.warning("%s: malformed tax year %r", object_id, tax_year)

    form_root = form.find(root, f"ReturnData/{FORM_ROOTS[return_type]}")
    if form_root is None:
        logger.warning(
            "%s: no %s element under ReturnData", object_id, FORM_ROOTS[return_type]
        )
        filing.update(dict.fromkeys(FORM_FIELDS))
        return ParsedFiling(filing)

    for column, paths in FORM_FIELDS.items():
        relative = paths.get(return_type)
        if relative is None:
            filing[column] = None
        elif column in TEXT_COLUMNS:
            filing[column] = form.text(form_root, relative, column)
        else:
            filing[column] = form.amount(form_root, relative, column)

    parsed = ParsedFiling(
        filing,
        programs=_parse_programs(form, form_root, return_type, filing["ein"]),
        revenue_lines=(
            _parse_revenue_lines(form, form_root, filing["ein"])
            if return_type == FORM_990
            else []
        ),
        schedule_o=_parse_schedule_o(form, root, return_type, filing["ein"]),
    )
    if form.missing:
        logger.info(
            "%s (%s): missing %s", object_id, return_type, ", ".join(form.missing)
        )
    return parsed


def parse_filing(path: Path) -> ParsedFiling:
    """Parse one `<OBJECT_ID>_public.xml` file into its rows.

    Raises UnsupportedForm for return types other than 990 and 990-EZ, and
    etree.XMLSyntaxError or OSError if the file can't be read as XML.
    `parse_filings` catches and counts all three.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    root = etree.parse(str(path), parser).getroot()
    return parse_root(root, object_id_from_path(path))


def _frame(
    rows: list[dict[str, Any]], columns: list[str], amounts: list[str]
) -> pd.DataFrame:
    """Build a DataFrame with fixed columns, amounts as nullable Int64."""
    df = pd.DataFrame(rows, columns=columns)
    for column in amounts:
        df[column] = df[column].astype("Int64")
    return df


def parse_filings(paths: Iterable[Path]) -> ParseResult:
    """Parse every file in `paths` into the four tables.

    Unsupported return types are counted in `skipped`. A file that isn't valid
    XML is logged and listed in `failed`. Neither stops the run.
    """
    filings, programs, revenue_lines, schedule_o = [], [], [], []
    skipped: Counter[str] = Counter()
    failed: list[str] = []
    unsupported: list[str] = []
    for path in paths:
        try:
            parsed = parse_filing(path)
        except UnsupportedForm as skip:
            logger.info("%s", skip)
            skipped[skip.return_type] += 1
            unsupported.append(object_id_from_path(path))
            continue
        except (etree.XMLSyntaxError, OSError) as error:
            logger.error("%s: could not parse XML: %s", path.name, error)
            failed.append(path.name)
            continue
        filings.append(parsed.filing)
        programs.extend(parsed.programs)
        revenue_lines.extend(parsed.revenue_lines)
        schedule_o.extend(parsed.schedule_o)

    return ParseResult(
        filings=_frame(
            filings, FILING_COLUMNS, [*AMOUNT_COLUMNS["filings"], "tax_year"]
        ),
        programs=_frame(programs, PROGRAM_COLUMNS, AMOUNT_COLUMNS["programs"]),
        revenue_lines=_frame(
            revenue_lines, REVENUE_LINE_COLUMNS, AMOUNT_COLUMNS["revenue_lines"]
        ),
        schedule_o=_frame(schedule_o, SCHEDULE_O_COLUMNS, []),
        skipped=dict(skipped),
        failed=failed,
        unsupported=unsupported,
    )


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write `df` to parquet at `path` through DuckDB, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.as_posix().replace("'", "''")
    with duckdb.connect() as con:
        con.register("frame", df)
        con.execute(f"COPY frame TO '{target}' (FORMAT parquet)")
    logger.info("Wrote %d rows to %s", len(df), path)


def missing_counts(filings: pd.DataFrame) -> pd.DataFrame:
    """Count None values per filing column and form, for columns that form has."""
    rows = []
    for return_type, group in filings.groupby("return_type"):
        for column, paths in FORM_FIELDS.items():
            if return_type in paths:
                rows.append(
                    {
                        "return_type": return_type,
                        "column": column,
                        "missing": int(group[column].isna().sum()),
                        "of": len(group),
                    }
                )
    return pd.DataFrame(rows, columns=["return_type", "column", "missing", "of"])


def run_parse(config: dict[str, Any], object_ids: Iterable[str]) -> ParseResult:
    """Parse the extracted XML for exactly these OBJECT_IDs and write the tables.

    Only manifest filings are read, so XML left in `xml_dir` by an older manifest
    is never loaded. IDs with no file on disk are listed in `not_on_disk`.
    Writes `paths.interim/<table>.parquet` for each of TABLE_NAMES.
    """
    folder = xml_dir(config)
    paths, not_on_disk = [], []
    for object_id in sorted(set(object_ids)):
        path = xml_path(folder, object_id)
        (paths if path.is_file() else not_on_disk).append(path)
    result = parse_filings(paths)
    result.not_on_disk = [object_id_from_path(p) for p in not_on_disk]
    if not_on_disk:
        logger.warning("%d manifest filings have no XML on disk", len(not_on_disk))

    interim = Path(config["paths"]["interim"])
    for name in TABLE_NAMES:
        write_parquet(getattr(result, name), interim / f"{name}.parquet")
    return result


def main() -> None:
    """Parse the manifest's XML, print a summary, and write the four tables."""
    config = load_config()
    interim = Path(config["paths"]["interim"])
    log_path = interim / "parse.log"
    # Every missing field goes to parse.log (INFO). Only warnings reach the console.
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    logfile = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    logfile.setLevel(logging.INFO)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        handlers=[console, logfile],
    )

    manifest = duckdb.read_parquet(manifest_path(config).as_posix()).df()
    result = run_parse(config, manifest["OBJECT_ID"])

    print(f"manifest filings        {len(manifest):>7,}")
    print(f"  no XML on disk        {len(result.not_on_disk):>7,}")
    for return_type, count in result.filings["return_type"].value_counts().items():
        print(f"  parsed {return_type:<15}{count:>7,}")
    for return_type, count in sorted(result.skipped.items()):
        print(f"  skipped {return_type:<14}{count:>7,}")
    print(f"  failed to parse       {len(result.failed):>7,}")
    print(f"program rows            {len(result.programs):>7,}")
    print(f"revenue line rows       {len(result.revenue_lines):>7,}")
    sched = result.schedule_o
    print(f"schedule O rows         {len(sched):>7,}")
    print(f"  mission or program    {int(sched['is_mission_or_program'].sum()):>7,}")
    print(f"  no part identified    {int(sched['part'].isna().sum()):>7,}")

    counts = missing_counts(result.filings)
    print("\nMissing (None) by field, for fields each form has:")
    print(counts.loc[counts["missing"] > 0].to_string(index=False))
    for name in TABLE_NAMES:
        print(f"Wrote {interim / f'{name}.parquet'}")
    print(f"Per-filing missing-field log: {log_path}")


if __name__ == "__main__":
    main()
