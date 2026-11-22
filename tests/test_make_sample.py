from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from evals.make_sample import (
    SAMPLE_COLUMNS,
    SHEET_COLUMNS,
    SHEET_ENCODING,
    SampleSettings,
    dev_counts,
    draw,
    sample_settings,
    select,
    write_new_csv,
    write_sheets,
)
from src.ingest.build import LABEL_SAMPLE_FILE, build, database_path
from tests.conftest import make_project

# The miniature project (tests/conftest.py) has these frames:
#   ntee_L with a parsed filing:        474942066, 920731806
#   ntee_adjacent with a parsed filing: 444444444
#   random (non-candidate, 990/990-EZ in the index): 555555555, not yet ingested
SETTINGS = SampleSettings(
    seed=7,
    groups={"ntee_L": 2, "ntee_adjacent": 1, "random": 1},
    random_reserves=1,
    dev_size=2,
)


@pytest.fixture
def project(tmp_path: Path) -> dict[str, Any]:
    config = make_project(tmp_path)
    build(config)
    return config


def connect(config: dict[str, Any]) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(database_path(config)), read_only=True)


def draw_and_rebuild(config: dict[str, Any]) -> pd.DataFrame:
    """Draw, save the sample where the build looks for it, and rebuild."""
    with connect(config) as con:
        sample = draw(con, SETTINGS)
    write_new_csv(sample, Path(config["paths"]["labels"]) / LABEL_SAMPLE_FILE)
    build(config)
    return sample


# Each group is drawn only from its own frame; the random group asks for one
# reserve that doesn't exist, which is allowed (logged), not an error.
def test_draw_uses_each_frame(project: dict[str, Any]) -> None:
    with connect(project) as con:
        sample = draw(con, SETTINGS)
    assert list(sample.columns) == SAMPLE_COLUMNS
    groups = sample.groupby("sample_group")["ein"].apply(set).to_dict()
    assert groups == {
        "ntee_L": {"474942066", "920731806"},  # 222222222 has no parsed filing
        "ntee_adjacent": {"444444444"},  # 111111111 filed only a 990-PF
        "random": {"555555555"},
    }
    assert set(sample["role"]) == {"sample"}


# The same seed and database give the same draw.
def test_draw_is_reproducible(project: dict[str, Any]) -> None:
    with connect(project) as con:
        first, second = draw(con, SETTINGS), draw(con, SETTINGS)
    columns = ["ein", "sample_group", "rank", "role"]
    pd.testing.assert_frame_equal(first[columns], second[columns])


# A group bigger than its frame is an error, not a silently short sample.
def test_draw_rejects_too_small_frame(project: dict[str, Any]) -> None:
    too_many = SampleSettings(7, {"ntee_L": 3, "ntee_adjacent": 1, "random": 1}, 0, 2)
    with connect(project) as con, pytest.raises(ValueError, match="ntee_L"):
        draw(con, too_many)


# The real split: 40/30/30 with a dev set of 40 gives 16/12/12. Odd splits
# still add up exactly.
def test_dev_counts() -> None:
    real = {"ntee_L": 40, "ntee_adjacent": 30, "random": 30}
    assert dev_counts(real, 40) == {"ntee_L": 16, "ntee_adjacent": 12, "random": 12}
    assert (
        sum(dev_counts({"ntee_L": 2, "ntee_adjacent": 1, "random": 1}, 2).values()) == 2
    )


# Settings must cover exactly the three groups and add up to the sample size.
def test_sample_settings_checks_config() -> None:
    config = {
        "label_sample_size": 100,
        "label_sample": {
            "seed": 1,
            "groups": {"ntee_L": 40, "ntee_adjacent": 30, "random": 30},
            "dev_size": 40,
        },
    }
    assert sample_settings(config).size == 100
    config["label_sample"]["groups"]["random"] = 20
    with pytest.raises(ValueError, match="add up to 90"):
        sample_settings(config)


# Neither the sample nor a sheet is ever overwritten.
def test_write_new_csv_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    write_new_csv(pd.DataFrame({"ein": ["1"]}), path)
    with pytest.raises(FileExistsError, match="never overwritten"):
        write_new_csv(pd.DataFrame({"ein": ["2"]}), path)
    assert pd.read_csv(path, dtype=str)["ein"].tolist() == ["1"]


# The random org isn't ingested until it's sampled; then the build picks it up.
def test_build_ingests_sampled_eins(project: dict[str, Any]) -> None:
    with connect(project) as con:
        assert con.execute(
            "SELECT count(*) FROM ingest.filings WHERE ein = '555555555'"
        ).fetchall() == [(0,)]
    draw_and_rebuild(project)
    with connect(project) as con:
        assert con.execute(
            "SELECT status FROM ingest.filing_index WHERE ein = '555555555'"
        ).fetchall() == [("parsed",)]
        assert con.execute(
            "SELECT count FROM ingest.funnel "
            "WHERE stage = 'eval sample EINs outside the candidates'"
        ).fetchall() == [(1,)]


# Writing sheets before the rebuild fails clearly: the random org has no text yet.
def test_select_before_rebuild_explains(project: dict[str, Any]) -> None:
    with connect(project) as con:
        sample = draw(con, SETTINGS)
        with pytest.raises(ValueError, match="python -m src.ingest.build"):
            select(con, sample, SETTINGS)


# A reserve stands in for a sampled org with no parsed filing, keeping rank order.
def test_reserve_fills_a_gap(project: dict[str, Any]) -> None:
    draw_and_rebuild(project)
    sample = pd.DataFrame(
        [
            ("474942066", "ntee_L", 1, "sample"),
            ("920731806", "ntee_L", 2, "sample"),
            ("444444444", "ntee_adjacent", 1, "sample"),
            ("333333333", "random", 1, "sample"),  # no filing at all
            ("555555555", "random", 2, "reserve"),
        ],
        columns=["ein", "sample_group", "rank", "role"],
    )
    with connect(project) as con:
        chosen = select(con, sample, SETTINGS)
    assert chosen.loc[chosen["sample_group"] == "random", "ein"].tolist() == [
        "555555555"
    ]


# The sheets: right split sizes, blind (no NTEE or group), text filled in,
# label and notes empty, and never overwritten.
def test_write_sheets(project: dict[str, Any], tmp_path: Path) -> None:
    sample = draw_and_rebuild(project)
    out = tmp_path / "sheets"
    with connect(project) as con:
        chosen = select(con, sample, SETTINGS)
        paths = write_sheets(con, chosen, SETTINGS, out)
        sheets = {
            split: pd.read_csv(
                path, dtype=str, keep_default_na=False, encoding=SHEET_ENCODING
            )
            for split, path in paths.items()
        }
        assert {split: len(df) for split, df in sheets.items()} == {"dev": 2, "test": 2}
        both = pd.concat(sheets.values())
        assert list(both.columns) == SHEET_COLUMNS
        assert not {"ntee_code", "sample_group", "candidate_group"} & set(both.columns)
        assert set(both["ein"]) == set(chosen["ein"])
        assert (both["label"] == "").all() and (both["notes"] == "").all()

        rent = both.loc[both["ein"] == "555555555"].iloc[0]
        assert rent["mission"].startswith("Emergency rent and utility help")
        assert (
            rent["programs"]
            == "1. Paid one month of rent for 12 households. (expenses $40)"
        )
        tiny = both.loc[both["ein"] == "474942066"].iloc[0]
        assert tiny["programs"].startswith("1. [4a] Smith Street Construction")
        assert "[Part III Line 4d] Chicken Coop" in tiny["schedule_o_text"]

        with pytest.raises(FileExistsError):
            write_sheets(con, chosen, SETTINGS, out)
