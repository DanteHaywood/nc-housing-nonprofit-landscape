"""Build the filing manifest: which 990 XML files to extract for candidate EINs.

Reads the yearly IRS index CSVs (`config.yaml: filing_years`), keeps rows for
candidate EINs from `src.ingest.bmf`, drops 990-T returns, and keeps the latest
filing per EIN and tax period by OBJECT_ID (D-010). The result goes to
`paths.interim/manifest.parquet`.

Run with `python -m src.ingest.manifest`.
"""

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.config import load_config
from src.ingest.bmf import load_candidates

logger = logging.getLogger(__name__)

# 990-T is a separate unrelated-business-income return with no mission or program
# text. Left in, it can win the dedupe over the organization's real 990 (D-010).
EXCLUDED_RETURN_TYPES = frozenset({"990T"})
OBJECT_ID_LENGTH = 18
DEDUPE_KEY = ["EIN", "TAX_PERIOD"]
OUTPUT_COLUMNS = [
    "EIN",
    "TAX_PERIOD",
    "RETURN_TYPE",
    "OBJECT_ID",
    "XML_BATCH_ID",
    "TAXPAYER_NAME",
    "SUB_DATE",
    "index_year",
    "candidate_group",
    "NTEE_CD",
]


def index_paths(config: dict[str, Any]) -> dict[int, Path]:
    """Map each of `filing_years` to its index CSV, e.g. 2024 -> raw/index_2024.csv."""
    raw = Path(config["paths"]["raw"])
    return {year: raw / f"index_{year}.csv" for year in config["filing_years"]}


def manifest_path(config: dict[str, Any]) -> Path:
    """Return the manifest output path, `paths.interim/manifest.parquet`."""
    return Path(config["paths"]["interim"]) / "manifest.parquet"


def load_index(path: Path, year: int) -> pd.DataFrame:
    """Read one index CSV with every column as a string, plus an `index_year` column.

    EINs are zero-padded to 9 digits. Raises ValueError if any EIN is not 9 digits
    or any OBJECT_ID is not 18 digits (dedupe sorts OBJECT_ID as a string, which
    is only correct at a fixed width).
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    df["EIN"] = df["EIN"].str.strip().str.zfill(9)
    df["OBJECT_ID"] = df["OBJECT_ID"].str.strip()
    df["RETURN_TYPE"] = df["RETURN_TYPE"].str.strip().str.upper()
    for column, pattern in [("EIN", r"\d{9}"), ("OBJECT_ID", r"\d{18}")]:
        bad = df.loc[~df[column].str.fullmatch(pattern), column]
        if not bad.empty:
            raise ValueError(
                f"{len(bad)} malformed {column}s in {path}, e.g. {bad.iloc[0]!r}"
            )
    df["index_year"] = str(year)
    logger.info("Loaded %d index rows from %s", len(df), path)
    return df


def join_candidates(index: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Keep index rows whose EIN is a candidate, adding `candidate_group` and `NTEE_CD`.

    Raises pandas MergeError if `candidates` repeats an EIN, since that would
    duplicate filings.
    """
    out = index.merge(
        candidates[["EIN", "candidate_group", "NTEE_CD"]],
        on="EIN",
        how="inner",
        validate="many_to_one",
    )
    logger.info(
        "Matched %d index rows for %d candidate EINs", len(out), out["EIN"].nunique()
    )
    return out


def drop_return_types(
    df: pd.DataFrame, excluded: Iterable[str] = EXCLUDED_RETURN_TYPES
) -> pd.DataFrame:
    """Drop rows whose RETURN_TYPE is in `excluded` (990T by default)."""
    keep = ~df["RETURN_TYPE"].isin(set(excluded))
    logger.info("Dropped %d rows with return types %s", (~keep).sum(), excluded)
    return df.loc[keep].reset_index(drop=True)


def dedupe_latest(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the row with the highest OBJECT_ID per EIN and TAX_PERIOD, across years.

    OBJECT_ID starts with the IRS processing year, so the highest one is the most
    recently processed filing (an amendment or later resubmission). SUB_DATE only
    holds the processing year, so it can't break ties within a year (D-010).
    Returns rows sorted by EIN and TAX_PERIOD.
    """
    out = (
        df.sort_values("OBJECT_ID", kind="stable")
        .drop_duplicates(DEDUPE_KEY, keep="last")
        .sort_values(DEDUPE_KEY)
        .reset_index(drop=True)
    )
    logger.info("Deduped %d rows to %d filings", len(df), len(out))
    return out


def write_manifest(df: pd.DataFrame, path: Path) -> None:
    """Write `df` to a parquet file at `path` through DuckDB, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.as_posix().replace("'", "''")
    with duckdb.connect() as con:
        con.register("manifest", df)
        con.execute(f"COPY manifest TO '{target}' (FORMAT parquet)")
    logger.info("Wrote %d rows to %s", len(df), path)


def build_manifest(
    config: dict[str, Any], candidates: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Run every manifest stage for `config`.

    `candidates` is the output of `bmf.filter_candidates`. If omitted, it is
    loaded from the BMF named by `config`. Returns the manifest (OUTPUT_COLUMNS
    only) and the row count after each stage.
    """
    paths = index_paths(config)
    counts: dict[str, int] = {}
    frames = []
    for year, path in paths.items():
        frame = load_index(path, year)
        counts[f"index_{year} rows"] = len(frame)
        frames.append(frame)
    index = pd.concat(frames, ignore_index=True)
    counts["index rows, all years"] = len(index)

    if candidates is None:
        candidates = load_candidates(config)
    counts["candidate EINs"] = len(candidates)

    matched = join_candidates(index, candidates)
    counts["rows matching candidates"] = len(matched)
    counts["candidate EINs with a filing"] = matched["EIN"].nunique()

    filtered = drop_return_types(matched)
    counts["rows after dropping 990T"] = len(filtered)

    manifest = dedupe_latest(filtered)[OUTPUT_COLUMNS]
    counts["filings after dedupe"] = len(manifest)
    counts["EINs in manifest"] = manifest["EIN"].nunique()
    return manifest, counts


def main() -> None:
    """Build the manifest from config.yaml, print stage counts, and write it."""
    logging.basicConfig(level=logging.WARNING)
    config = load_config()
    manifest, counts = build_manifest(config)
    width = max(len(name) for name in counts)
    for name, count in counts.items():
        print(f"{name:<{width}}  {count:>9,}")
    by_type = manifest["RETURN_TYPE"].value_counts().to_dict()
    print(f"{'filings by return type':<{width}}  {by_type}")
    out = manifest_path(config)
    write_manifest(manifest, out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
