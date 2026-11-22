import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.ingest.bmf import GROUP_ADJACENT, GROUP_L, filter_candidates, load_bmf
from src.ingest.manifest import (
    OUTPUT_COLUMNS,
    build_manifest,
    dedupe_latest,
    drop_return_types,
    index_paths,
    join_candidates,
    load_index,
    write_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures"
# Hand-built index rows in the real IRS column layout. EINs come from the BMF
# fixture. Cases: an amended filing (010601868), the same period filed in both
# years (237078343), a 990 plus a 990T (030571456), two tax periods (010968313),
# an 8-digit EIN (43640604), and a non-candidate (010518008, NTEE B99).
INDEX_2024 = FIXTURES / "index_2024_sample.csv"
INDEX_2025 = FIXTURES / "index_2025_sample.csv"
BMF = FIXTURES / "eo_nc_sample.csv"
# Same list as config.yaml: ntee_adjacent (D-009), copied so tests don't read config.
ADJACENT = ["P60", "P70", "P73", "P75", "P85", "S20", "S21", "S22", "S30", "S31"]

# The filing expected to survive for each EIN and tax period, by OBJECT_ID.
EXPECTED = {
    ("010601868", "202312"): "202430229349200002",  # amended, later in 2024
    ("010968313", "202312"): "202420219349200045",
    ("010968313", "202412"): "202520219349200070",
    ("030571456", "202312"): "202411119349200020",  # the 990EZ, not the 990T
    ("043640604", "202312"): "202420219349200040",  # EIN padded from 8 digits
    ("237078343", "202306"): "202510139349300060",  # 2025 refiling wins over 2024
    ("510138261", "202412"): "202520139349300080",
}


# Both years' fixture rows, loaded the same way the pipeline loads them.
@pytest.fixture
def index() -> pd.DataFrame:
    return pd.concat(
        [load_index(INDEX_2024, 2024), load_index(INDEX_2025, 2025)],
        ignore_index=True,
    )


@pytest.fixture
def candidates() -> pd.DataFrame:
    return filter_candidates(load_bmf(BMF), "NC", ADJACENT)


def as_lookup(df: pd.DataFrame) -> dict[tuple[str, str], str]:
    return {
        (ein, period): object_id
        for ein, period, object_id in zip(
            df["EIN"], df["TAX_PERIOD"], df["OBJECT_ID"], strict=True
        )
    }


# Index paths come from config (raw dir + filing_years), not hardcoded.
def test_index_paths_use_raw_dir_and_years() -> None:
    config = {"paths": {"raw": "data/raw"}, "filing_years": [2024, 2025]}
    assert index_paths(config) == {
        2024: Path("data/raw/index_2024.csv"),
        2025: Path("data/raw/index_2025.csv"),
    }


# EINs load as 9-digit strings (the 8-digit one is padded) and each row is
# tagged with the year of the index file it came from.
def test_load_index_pads_eins_and_tags_year(index: pd.DataFrame) -> None:
    assert index["EIN"].str.fullmatch(r"\d{9}").all()
    assert "043640604" in set(index["EIN"])
    assert index["index_year"].value_counts().to_dict() == {"2024": 8, "2025": 3}


# A non-digit EIN raises instead of being silently fixed or dropped.
def test_malformed_ein_raises(tmp_path: Path) -> None:
    path = tmp_path / "index.csv"
    path.write_text(
        "EIN,TAX_PERIOD,RETURN_TYPE,OBJECT_ID\n12-345678,202312,990,"
        "202410229349200001\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed EIN"):
        load_index(path, 2024)


# A short OBJECT_ID raises, because dedupe sorts OBJECT_ID as a fixed-width string.
def test_malformed_object_id_raises(tmp_path: Path) -> None:
    path = tmp_path / "index.csv"
    path.write_text(
        "EIN,TAX_PERIOD,RETURN_TYPE,OBJECT_ID\n010601868,202312,990,2024102293\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed OBJECT_ID"):
        load_index(path, 2024)


# The join keeps only candidate EINs and brings in each one's candidate group.
def test_join_candidates_drops_non_candidates(
    index: pd.DataFrame, candidates: pd.DataFrame
) -> None:
    out = join_candidates(index, candidates)
    assert "010518008" not in set(out["EIN"])
    assert len(out) == len(index) - 1
    groups = dict(zip(out["EIN"], out["candidate_group"], strict=False))
    assert groups["010601868"] == GROUP_L
    assert groups["043640604"] == GROUP_ADJACENT


# A BMF with a repeated EIN raises rather than duplicating that EIN's filings.
def test_join_candidates_rejects_duplicate_candidates(
    index: pd.DataFrame, candidates: pd.DataFrame
) -> None:
    doubled = pd.concat([candidates, candidates.head(1)], ignore_index=True)
    with pytest.raises(pd.errors.MergeError):
        join_candidates(index, doubled)


# 990T rows are dropped, and the same organization's 990EZ stays.
def test_drop_return_types_removes_990t(index: pd.DataFrame) -> None:
    out = drop_return_types(index)
    assert "990T" not in set(out["RETURN_TYPE"])
    assert len(out) == len(index) - 1
    assert "202411119349200020" in set(out["OBJECT_ID"])


# Dedupe keeps the highest OBJECT_ID within a year and across years, and keeps
# different tax periods for the same EIN as separate filings.
def test_dedupe_latest_keeps_highest_object_id(
    index: pd.DataFrame, candidates: pd.DataFrame
) -> None:
    filtered = drop_return_types(join_candidates(index, candidates))
    out = dedupe_latest(filtered)
    assert as_lookup(out) == EXPECTED
    assert not out.duplicated(["EIN", "TAX_PERIOD"]).any()


# The manifest round-trips through parquet with EINs still stored as strings.
def test_write_manifest_round_trips(tmp_path: Path, index: pd.DataFrame) -> None:
    path = tmp_path / "interim" / "manifest.parquet"
    write_manifest(index, path)
    back = duckdb.sql(f"SELECT * FROM read_parquet('{path.as_posix()}')").df()
    assert len(back) == len(index)
    assert back["EIN"].tolist() == index["EIN"].tolist()
    ein_types = duckdb.sql(
        f"SELECT DISTINCT typeof(EIN) FROM read_parquet('{path.as_posix()}')"
    ).fetchall()
    assert ein_types == [("VARCHAR",)]


# End to end from a config: fixture files laid out like data/raw, with the
# stage counts the CLI prints.
def test_build_manifest_end_to_end(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    shutil.copy(INDEX_2024, raw / "index_2024.csv")
    shutil.copy(INDEX_2025, raw / "index_2025.csv")
    shutil.copy(BMF, raw / "eo_nc.csv")
    config = {
        "paths": {"raw": str(raw), "interim": str(tmp_path / "interim")},
        "state": "NC",
        "filing_years": [2024, 2025],
        "ntee_adjacent": ADJACENT,
    }
    manifest, counts = build_manifest(config)
    assert list(manifest.columns) == OUTPUT_COLUMNS
    assert as_lookup(manifest) == EXPECTED
    assert counts == {
        "index_2024 rows": 8,
        "index_2025 rows": 3,
        "index rows, all years": 11,
        "candidate EINs": 6,
        "rows matching candidates": 10,
        "candidate EINs with a filing": 6,
        "rows after dropping 990T": 9,
        "filings after dedupe": 7,
        "EINs in manifest": 6,
    }
