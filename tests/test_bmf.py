from pathlib import Path

import pandas as pd
import pytest

from src.ingest.bmf import (
    GROUP_ADJACENT,
    GROUP_L,
    bmf_path,
    filter_candidates,
    load_bmf,
)

# Ten real rows from the NC BMF: 3 L codes, 3 adjacent codes, 4 that should drop.
FIXTURE = Path(__file__).parent / "fixtures" / "eo_nc_sample.csv"
# Same list as config.yaml: ntee_adjacent (D-009), copied so tests don't read config.
ADJACENT = ["P60", "P70", "P73", "P75", "P85", "S20", "S21", "S22", "S30", "S31"]


# Loads the fixture once per test so each test starts from the same DataFrame.
@pytest.fixture
def bmf() -> pd.DataFrame:
    return load_bmf(FIXTURE)


# The BMF path is built from config (raw dir + lowercase state), not hardcoded.
def test_bmf_path_uses_raw_dir_and_state() -> None:
    config = {"paths": {"raw": "data/raw"}, "state": "NC"}
    assert bmf_path(config) == Path("data/raw/eo_nc.csv")


# Every EIN loads as a 9-digit string, and leading zeros survive (010601868).
def test_eins_are_nine_digit_strings(bmf: pd.DataFrame) -> None:
    assert len(bmf) == 10
    assert bmf["EIN"].map(type).eq(str).all()
    assert bmf["EIN"].str.fullmatch(r"\d{9}").all()
    assert "010601868" in set(bmf["EIN"])


# An EIN missing its leading zero (8 digits) is padded back to 9.
def test_short_ein_is_zero_padded(tmp_path: Path) -> None:
    path = tmp_path / "bmf.csv"
    path.write_text("EIN,STATE,NTEE_CD\n10601868,NC,L20\n", encoding="utf-8")
    assert load_bmf(path)["EIN"].tolist() == ["010601868"]


# An EIN with non-digits raises instead of being silently fixed or dropped.
def test_malformed_ein_raises(tmp_path: Path) -> None:
    path = tmp_path / "bmf.csv"
    path.write_text("EIN,STATE,NTEE_CD\n12-345678,NC,L20\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed EIN"):
        load_bmf(path)


# The filter keeps exactly the L and adjacent rows, including suffixed codes
# (L20Z, P73Z), and tags each one with the right candidate group.
def test_filter_candidates_selects_l_and_adjacent(bmf: pd.DataFrame) -> None:
    out = filter_candidates(bmf, "NC", ADJACENT)
    groups = dict(zip(out["EIN"], out["candidate_group"], strict=True))
    assert groups == {
        "010601868": GROUP_L,  # L20
        "237078343": GROUP_L,  # L20Z
        "030571456": GROUP_L,  # L21
        "043640604": GROUP_ADJACENT,  # P85
        "010968313": GROUP_ADJACENT,  # S20
        "510138261": GROUP_ADJACENT,  # P73Z
    }


# Codes outside the list drop out: P20 and S41 (same major groups as adjacent
# codes but not listed), B99 (unrelated), and a blank NTEE code.
def test_filter_candidates_excludes_other_codes(bmf: pd.DataFrame) -> None:
    excluded = {"010590995", "010690733", "010518008", "010477310"}  # P20 S41 B99 ""
    out = filter_candidates(bmf, "NC", ADJACENT)
    assert excluded.isdisjoint(out["EIN"])


# Rows outside the requested state are dropped; the NC fixture has no SC rows.
def test_filter_candidates_respects_state(bmf: pd.DataFrame) -> None:
    assert filter_candidates(bmf, "SC", ADJACENT).empty
