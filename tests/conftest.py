"""Shared setup: a miniature raw/ folder that runs the whole ingest pipeline.

`make_project` writes, under a temporary root, everything `src.ingest.build`
reads: a state BMF, two yearly index CSVs, and one IRS-style zip. The filings
are the two real fixture XMLs plus two synthetic ones, chosen so that every
`filing_index.status` path and every funnel step has something in it:

| EIN       | NTEE | Filing                          | Expected status   |
|-----------|------|---------------------------------|-------------------|
| 474942066 | L40  | real 990 (Tiny House)           | parsed            |
|           |      | + a 990T row                    | dropped (990T)    |
| 920731806 | L40  | real 990-EZ (Lifes Good)        | parsed            |
|           |      | + an older row, same period     | dropped (dedupe)  |
| 444444444 | L30  | synthetic 990-EZ, bare pointer  | parsed            |
| 111111111 | P85  | synthetic 990-PF                | unsupported_form  |
| 222222222 | L20  | listed in the index, in no zip  | not_in_zip        |
| 333333333 | none | not a candidate                 | (no filing)       |
"""

import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest

from src.ingest.build import build

FIXTURE_XML = Path(__file__).parent / "fixtures" / "xml"
REAL_990 = "202522749349301342"
REAL_990EZ = "202523219349202597"
POINTER_990EZ = "202510009349200004"
PF = "202510009349100001"
NOT_IN_ZIP = "202410009349300002"
DROPPED_990T = "202510009349300005"
OLDER_DUPLICATE = "202410009349200003"
STRAY = "202599999999999999"  # left in the XML folder, not in the manifest
ADJACENT = ["P60", "P70", "P73", "P75", "P85", "S20", "S21", "S22", "S30", "S31"]

BMF_HEADER = (
    "EIN,NAME,STREET,CITY,STATE,ZIP,NTEE_CD,SUBSECTION,FOUNDATION,RULING,"
    "TAX_PERIOD,ASSET_AMT,INCOME_AMT,REVENUE_AMT"
)
# (EIN, name, ZIP, NTEE, asset, income, revenue); the other columns are filler.
BMF_ROWS = [
    ("474942066", "TINY HOUSE COMMUNITY DEVELOPMENT INC", "27420-0691", "L40",
     "1581093", "714067", "714067"),
    ("920731806", "LIFES GOOD STAY HERE", "28202-0000", "L40",
     "16338", "179716", "179716"),
    ("444444444", "POINTER HOUSING INC", "28401-1234", "L30", "0", "0", "0"),
    ("111111111", "SAMPLE FOUNDATION", "27601-0000", "P85",
     "500000", "20000", "20000"),
    ("222222222", "MISSING FILING INC", "27701-0000", "L20", "", "", ""),
    ("333333333", "UNCODED ORG", "28801-0000", "", "10", "10", "10"),
]  # fmt: skip

INDEX_HEADER = (
    "RETURN_ID,FILING_TYPE,EIN,TAX_PERIOD,SUB_DATE,TAXPAYER_NAME,"
    "RETURN_TYPE,DLN,OBJECT_ID,XML_BATCH_ID"
)
# (EIN, tax period, return type, OBJECT_ID) per yearly index.
INDEX_ROWS = {
    2024: [
        ("222222222", "202312", "990", NOT_IN_ZIP),
        ("920731806", "202412", "990EZ", OLDER_DUPLICATE),
    ],
    2025: [
        ("474942066", "202412", "990", REAL_990),
        ("474942066", "202412", "990T", DROPPED_990T),
        ("920731806", "202412", "990EZ", REAL_990EZ),
        ("444444444", "202412", "990EZ", POINTER_990EZ),
        ("111111111", "202412", "990PF", PF),
    ],
}


def bmf_csv() -> str:
    lines = [BMF_HEADER]
    for ein, name, zip_code, ntee, asset, income, revenue in BMF_ROWS:
        filler = f"1 MAIN ST,ANYTOWN,NC,{zip_code},{ntee},03,15,200001,202412"
        lines.append(f"{ein},{name},{filler},{asset},{income},{revenue}")
    return "\n".join(lines) + "\n"


def index_csv(year: int) -> str:
    lines = [INDEX_HEADER]
    for number, (ein, period, form, object_id) in enumerate(INDEX_ROWS[year]):
        batch = f"{year}_TEOS_XML_01A"
        row = [str(number), "EFILE", ein, period, str(year), "ORG", form, "0"]
        lines.append(",".join([*row, object_id, batch]))
    return "\n".join(lines) + "\n"


def synthetic_xml(ein: str, return_type: str, form: str) -> str:
    """A minimal return in the IRS namespace with `form` inside ReturnData."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Return xmlns="http://www.irs.gov/efile">
  <ReturnHeader>
    <TaxYr>2024</TaxYr><ReturnTypeCd>{return_type}</ReturnTypeCd>
    <Filer><EIN>{ein}</EIN><BusinessName>
      <BusinessNameLine1Txt>SYNTHETIC</BusinessNameLine1Txt></BusinessName></Filer>
  </ReturnHeader>
  <ReturnData>{form}</ReturnData>
</Return>"""


# Mission is only a pointer, and there's no total revenue and no program.
POINTER_EZ_FORM = (
    "<IRS990EZ><PrimaryExemptPurposeTxt>SEE SCHEDULE O</PrimaryExemptPurposeTxt>"
    "<ContributionsGiftsGrantsEtcAmt>100</ContributionsGiftsGrantsEtcAmt>"
    "<TotalExpensesAmt>90</TotalExpensesAmt>"
    "<NetAssetsOrFundBalancesGrp><BOYAmt>0</BOYAmt><EOYAmt>10</EOYAmt>"
    "</NetAssetsOrFundBalancesGrp></IRS990EZ>"
)


def make_project(root: Path) -> dict[str, Any]:
    """Write the miniature raw/ folder under `root` and return a matching config."""
    raw = root / "raw"
    raw.mkdir(parents=True)
    (raw / "eo_nc.csv").write_text(bmf_csv(), encoding="utf-8")
    for year in INDEX_ROWS:
        (raw / f"index_{year}.csv").write_text(index_csv(year), encoding="utf-8")
    members = {
        REAL_990: (FIXTURE_XML / f"{REAL_990}_public.xml").read_bytes(),
        REAL_990EZ: (FIXTURE_XML / f"{REAL_990EZ}_public.xml").read_bytes(),
        POINTER_990EZ: synthetic_xml("444444444", "990EZ", POINTER_EZ_FORM).encode(),
        PF: synthetic_xml("111111111", "990PF", "<IRS990PF/>").encode(),
    }
    with zipfile.ZipFile(raw / "2025_TEOS_XML_01A.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for object_id, data in members.items():
            z.writestr(f"{object_id}_public.xml", data)

    # A leftover XML from some older manifest. The build must not load it.
    stray = root / "interim" / "xml"
    stray.mkdir(parents=True)
    shutil.copy(FIXTURE_XML / f"{REAL_990}_public.xml", stray / f"{STRAY}_public.xml")
    return {
        "paths": {
            "raw": str(raw),
            "interim": str(root / "interim"),
            "processed": str(root / "processed" / "landscape.duckdb"),
        },
        "state": "NC",
        "filing_years": [2024, 2025],
        "ntee_adjacent": ADJACENT,
    }


# One build shared by the read-only tests in test_build.py and test_quality.py.
@pytest.fixture(scope="session")
def built_config(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    config = make_project(tmp_path_factory.mktemp("project"))
    build(config)
    return config
