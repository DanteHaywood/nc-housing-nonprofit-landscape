"""Run the whole ingest pipeline and load the DuckDB database (D-014).

bmf -> manifest -> extract -> parse, then one transaction that replaces the
`ingest` schema in `paths.processed` with fresh tables (see schema.sql and
docs/schema.md). If anything fails, the transaction rolls back and the previous
database is left as it was. Other schemas, such as `classify`, are never touched.

Each stage still writes its own files to `paths.interim` (manifest.parquet,
the extracted XML, the parsed parquet tables), so stages can also be run alone.

Run with `python -m src.ingest.build`, then `python -m src.ingest.quality`.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.config import load_config
from src.ingest.bmf import bmf_path, filter_candidates, load_bmf
from src.ingest.extract import ExtractSummary, run_extract
from src.ingest.manifest import (
    build_manifest,
    latest_filings_for,
    load_indexes,
    manifest_path,
    write_manifest,
)
from src.ingest.parse import FORM_990, FORM_990EZ, ParseResult, run_parse

logger = logging.getLogger(__name__)

SCHEMA_SQL = Path(__file__).with_name("schema.sql")
SCHEMA = "ingest"
# Load order: parents before children, so foreign keys check as rows go in.
LOAD_ORDER = [
    "organizations",
    "state_index",
    "filing_index",
    "filings",
    "programs",
    "revenue_lines",
    "schedule_o",
    "funnel",
]
BMF_AMOUNTS = {
    "ASSET_AMT": "bmf_asset_amt",
    "INCOME_AMT": "bmf_income_amt",
    "REVENUE_AMT": "bmf_revenue_amt",
}
BMF_TEXT = {
    "EIN": "ein",
    "NAME": "name",
    "STREET": "street",
    "CITY": "city",
    "STATE": "state",
    "ZIP": "zip",
    "NTEE_CD": "ntee_code",
    "SUBSECTION": "subsection",
    "FOUNDATION": "foundation_code",
    "RULING": "ruling",
    "TAX_PERIOD": "bmf_tax_period",
}
# The eval's sample (evals/make_sample.py, D-015). Its EINs are ingested too.
LABEL_SAMPLE_FILE = "sample.csv"
STATE_INDEX_COLUMNS = {
    "OBJECT_ID": "object_id",
    "EIN": "ein",
    "TAX_PERIOD": "tax_period",
    "RETURN_TYPE": "return_type",
    "index_year": "index_year",
}
MANIFEST_COLUMNS = {
    "OBJECT_ID": "object_id",
    "EIN": "ein",
    "TAX_PERIOD": "tax_period",
    "RETURN_TYPE": "return_type",
    "TAXPAYER_NAME": "taxpayer_name",
    "index_year": "index_year",
    "XML_BATCH_ID": "xml_batch_id",
}


@dataclass
class BuildResult:
    """The loaded tables (as DataFrames) and the database they went into."""

    tables: dict[str, pd.DataFrame]
    database: Path


def database_path(config: dict[str, Any]) -> Path:
    """Return the DuckDB file named by `config.yaml: paths.processed`."""
    return Path(config["paths"]["processed"])


def label_sample_eins(config: dict[str, Any]) -> set[str]:
    """EINs in the eval's sample file, if `paths.labels/sample.csv` exists.

    Reserves are included, so every organization the sheet might use is
    ingested. Returns an empty set when there is no sample yet.
    """
    labels = config["paths"].get("labels")
    if labels is None:
        return set()
    path = Path(labels) / LABEL_SAMPLE_FILE
    if not path.is_file():
        return set()
    sample = pd.read_csv(path, dtype=str, keep_default_na=False)
    return set(sample["ein"].str.zfill(9))


def extra_frame(
    bmf: pd.DataFrame, candidates: pd.DataFrame, eins: set[str]
) -> pd.DataFrame:
    """BMF rows for `eins` that aren't candidates, shaped like `candidates`.

    Their `candidate_group` is None. EINs missing from the BMF are logged and
    skipped.
    """
    missing = eins - set(bmf["EIN"])
    if missing:
        logger.warning("%d sample EINs are not in the BMF; skipped", len(missing))
    rows = bmf.loc[bmf["EIN"].isin(eins) & ~bmf["EIN"].isin(candidates["EIN"])]
    return rows.assign(candidate_group=None).reset_index(drop=True)


def state_index_frame(index: pd.DataFrame, eins: pd.Series) -> pd.DataFrame:
    """`ingest.state_index` rows: every BMF organization's index filings.

    Same rule as the manifest (990-T dropped, latest per EIN and tax period),
    but for every organization in the state, ingested or not. This is the frame
    the eval's random group is drawn from (D-015).
    """
    rows = latest_filings_for(index, eins)
    out = rows[list(STATE_INDEX_COLUMNS)].rename(columns=STATE_INDEX_COLUMNS)
    out["index_year"] = out["index_year"].astype(int)
    return out.reset_index(drop=True)


def _blank_to_none(series: pd.Series) -> pd.Series:
    """Strip strings and turn empty ones into None (BMF blanks arrive as "")."""
    stripped = series.str.strip()
    return stripped.where(stripped != "", None)


def organizations_frame(bmf: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Turn the full BMF into `ingest.organizations` rows.

    `bmf` is `load_bmf` output (every column a string, EINs padded) and
    `candidates` is `filter_candidates` output. BMF amounts become nullable
    integers. A non-numeric amount raises, the same as a malformed EIN does.
    """
    out = pd.DataFrame({new: _blank_to_none(bmf[old]) for old, new in BMF_TEXT.items()})
    out["ein"] = bmf["EIN"]
    zip_digits = out["zip"].str[:5]
    out["zip5"] = zip_digits.where(zip_digits.str.fullmatch(r"\d{5}").fillna(False))
    out["ntee_major_group"] = out["ntee_code"].str[:1]
    groups = dict(zip(candidates["EIN"], candidates["candidate_group"], strict=True))
    out["candidate_group"] = out["ein"].map(groups)
    for old, new in BMF_AMOUNTS.items():
        out[new] = pd.to_numeric(_blank_to_none(bmf[old]), errors="raise").astype(
            "Int64"
        )
    return out


def filing_statuses(
    manifest: pd.DataFrame, extract: ExtractSummary, parse: ParseResult
) -> pd.Series:
    """Return each manifest OBJECT_ID's outcome, indexed by OBJECT_ID.

    Checked in pipeline order, so each filing gets the first step it failed at:
    not_in_zip, unreadable, not_extracted, unsupported_form, parse_failed, parsed.
    """
    failed = {name.removesuffix("_public.xml") for name in parse.failed}
    parsed = set(parse.filings["object_id"])
    not_on_disk = set(parse.not_on_disk)
    unreadable = set(extract.result.unreadable)
    unsupported = set(parse.unsupported)

    def status(object_id: str) -> str:
        if object_id not in extract.located:
            return "not_in_zip"
        if object_id in unreadable:
            return "unreadable"
        if object_id in not_on_disk:
            return "not_extracted"
        if object_id in unsupported:
            return "unsupported_form"
        if object_id in failed:
            return "parse_failed"
        if object_id in parsed:
            return "parsed"
        raise ValueError(f"{object_id}: no outcome recorded by extract or parse")

    ids = manifest["OBJECT_ID"]
    return pd.Series([status(oid) for oid in ids], index=ids.to_numpy())


def filing_index_frame(
    manifest: pd.DataFrame, extract: ExtractSummary, parse: ParseResult
) -> pd.DataFrame:
    """Turn the manifest into `ingest.filing_index` rows with a status each."""
    out = manifest[list(MANIFEST_COLUMNS)].rename(columns=MANIFEST_COLUMNS)
    out["index_year"] = out["index_year"].astype(int)
    out["zip_name"] = out["object_id"].map(
        {oid: item.zip_path.stem for oid, item in extract.located.items()}
    )
    out["status"] = out["object_id"].map(filing_statuses(manifest, extract, parse))
    return out.reset_index(drop=True)


def _with_index_keys(
    df: pd.DataFrame, filing_index: pd.DataFrame, columns: list[str]
) -> pd.DataFrame:
    """Replace `ein` (and add `columns`) with the index's values for each filing.

    The index is the authority for EIN and tax period. A parsed EIN that
    disagrees (or is None because it was malformed) is logged and overridden.
    """
    keys = filing_index.set_index("object_id")
    out = df.copy()
    index_ein = out["object_id"].map(keys["ein"])
    differs = out["ein"].notna() & (out["ein"] != index_ein)
    if differs.any() or out["ein"].isna().any():
        logger.warning(
            "%d parsed rows had an EIN different from the index (or none); "
            "using the index EIN",
            int(differs.sum() + out["ein"].isna().sum()),
        )
    out["ein"] = index_ein
    for column in columns:
        out[column] = out["object_id"].map(keys[column])
    return out


def funnel_frame(rows: list[tuple[str, int]], built_at: datetime) -> pd.DataFrame:
    """Number the (stage, count) rows in order for `ingest.funnel`."""
    return pd.DataFrame(
        {
            "step": range(1, len(rows) + 1),
            "stage": [stage for stage, _ in rows],
            "count": [count for _, count in rows],
            "built_at": built_at,
        }
    )


def funnel_rows(
    organizations: pd.DataFrame,
    state_index: pd.DataFrame,
    manifest_counts: dict[str, int],
    extract: ExtractSummary,
    parse: ParseResult,
    config: dict[str, Any],
) -> list[tuple[str, int]]:
    """The pipeline's row counts, in order, from BMF to parsed filings."""
    groups = organizations["candidate_group"].value_counts()
    years = ", ".join(str(year) for year in config["filing_years"])
    by_form = parse.filings["return_type"].value_counts()
    on_disk = len(extract.result.extracted) + len(extract.result.skipped)
    return [
        ("BMF organizations", len(organizations)),
        ("candidates: NTEE L", int(groups.get("ntee_L", 0))),
        ("candidates: adjacent NTEE", int(groups.get("ntee_adjacent", 0))),
        ("eval sample EINs outside the candidates", manifest_counts["extra EINs"]),
        (f"index rows, {years}", manifest_counts["index rows, all years"]),
        ("state filings in the index (no 990T, deduped)", len(state_index)),
        (
            "index rows matching a selected EIN",
            manifest_counts["rows matching candidates"],
        ),
        (
            "selected EINs with an index row",
            manifest_counts["candidate EINs with a filing"],
        ),
        ("rows after dropping 990T", manifest_counts["rows after dropping 990T"]),
        ("filings after dedupe (manifest)", manifest_counts["filings after dedupe"]),
        ("located in a zip", len(extract.located)),
        ("XML on disk", on_disk),
        ("parsed: 990", int(by_form.get(FORM_990, 0))),
        ("parsed: 990EZ", int(by_form.get(FORM_990EZ, 0))),
        ("skipped: unsupported form", len(parse.unsupported)),
        ("failed to parse", len(parse.failed)),
    ]


def load_database(path: Path, tables: dict[str, pd.DataFrame]) -> None:
    """Replace the `ingest` schema in the DuckDB file at `path` with `tables`.

    Runs schema.sql and inserts each table by column name, all in one
    transaction. On any error the transaction rolls back, the previous `ingest`
    tables stay as they were, and the error is raised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    with duckdb.connect(str(path)) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            con.execute(ddl)
            for name in LOAD_ORDER:
                con.register("frame", tables[name])
                con.execute(f"INSERT INTO {SCHEMA}.{name} BY NAME SELECT * FROM frame")
                con.unregister("frame")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    logger.info("Loaded %s into %s", ", ".join(LOAD_ORDER), path)


def build(config: dict[str, Any]) -> BuildResult:
    """Run bmf -> manifest -> extract -> parse and load the database for `config`."""
    bmf = load_bmf(bmf_path(config))
    candidates = filter_candidates(bmf, config["state"], config["ntee_adjacent"])
    organizations = organizations_frame(bmf, candidates)
    extra = extra_frame(bmf, candidates, label_sample_eins(config))

    loaded = load_indexes(config)
    state_index = state_index_frame(loaded[0], organizations["ein"])
    manifest, manifest_counts = build_manifest(config, candidates, extra, loaded)
    write_manifest(manifest, manifest_path(config))
    extract = run_extract(config, manifest)
    # Filings in no zip were already reported by extract; parse only the rest.
    parse = run_parse(config, list(extract.located))

    filing_index = filing_index_frame(manifest, extract, parse)
    tables = {
        "organizations": organizations,
        "state_index": state_index,
        "filing_index": filing_index,
        "filings": _with_index_keys(parse.filings, filing_index, ["tax_period"]),
        "programs": _with_index_keys(parse.programs, filing_index, []),
        "revenue_lines": _with_index_keys(parse.revenue_lines, filing_index, []),
        "schedule_o": _with_index_keys(parse.schedule_o, filing_index, []),
        "funnel": funnel_frame(
            funnel_rows(
                organizations, state_index, manifest_counts, extract, parse, config
            ),
            datetime.now(),
        ),
    }
    database = database_path(config)
    load_database(database, tables)
    return BuildResult(tables=tables, database=database)


def main() -> None:
    """Build the database from config.yaml and print the row count of each table."""
    config = load_config()
    interim = Path(config["paths"]["interim"])
    interim.mkdir(parents=True, exist_ok=True)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    logfile = logging.FileHandler(interim / "build.log", mode="w", encoding="utf-8")
    logfile.setLevel(logging.INFO)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        handlers=[console, logfile],
    )

    result = build(config)
    print(f"Loaded {result.database} (schema {SCHEMA}):")
    for name in LOAD_ORDER:
        print(f"  {name:<15}{len(result.tables[name]):>8,}")
    statuses = result.tables["filing_index"]["status"].value_counts().to_dict()
    print(f"filing_index status: {statuses}")
    print(f"Log: {interim / 'build.log'}. Next: python -m src.ingest.quality")


if __name__ == "__main__":
    main()
