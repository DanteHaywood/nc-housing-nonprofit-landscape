"""Load the IRS EO Business Master File and select candidate organizations.

Candidates are organizations whose NTEE code is in major group L (housing) or
in a short list of adjacent human services and community development codes
(`config.yaml: ntee_adjacent`, see D-009).
"""

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

HOUSING_MAJOR_GROUP = "L"
GROUP_L = "ntee_L"
GROUP_ADJACENT = "ntee_adjacent"


def bmf_path(config: dict[str, Any]) -> Path:
    """Return the path to the state BMF CSV, e.g. data/raw/eo_nc.csv."""
    return Path(config["paths"]["raw"]) / f"eo_{config['state'].lower()}.csv"


def load_bmf(path: Path) -> pd.DataFrame:
    """Read a BMF CSV with every column as a string.

    EINs are zero-padded to 9 digits and NTEE codes are stripped and uppercased.
    Raises ValueError if any EIN is not 9 digits.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    df["EIN"] = df["EIN"].str.strip().str.zfill(9)
    bad = df.loc[~df["EIN"].str.fullmatch(r"\d{9}"), "EIN"]
    if not bad.empty:
        raise ValueError(f"{len(bad)} malformed EINs in {path}, e.g. {bad.iloc[0]!r}")
    df["NTEE_CD"] = df["NTEE_CD"].str.strip().str.upper()
    logger.info("Loaded %d BMF rows from %s", len(df), path)
    return df


def filter_candidates(
    df: pd.DataFrame, state: str, adjacent: list[str]
) -> pd.DataFrame:
    """Keep rows in `state` whose NTEE code is in group L or in `adjacent`.

    `adjacent` codes are matched on the first 3 characters of NTEE_CD. Rows with
    a blank NTEE code are excluded. Returns a copy with a `candidate_group`
    column set to "ntee_L" or "ntee_adjacent".
    """
    ntee = df["NTEE_CD"]
    in_state = df["STATE"].str.strip().str.upper() == state.upper()
    is_l = ntee.str.startswith(HOUSING_MAJOR_GROUP)
    is_adjacent = ntee.str[:3].isin({code.upper() for code in adjacent})

    out = df.loc[in_state & (is_l | is_adjacent)].copy()
    out["candidate_group"] = GROUP_ADJACENT
    out.loc[is_l[out.index], "candidate_group"] = GROUP_L
    logger.info(
        "Selected %d candidates (%s)",
        len(out),
        out["candidate_group"].value_counts().to_dict(),
    )
    return out.reset_index(drop=True)


def load_candidates(config: dict[str, Any]) -> pd.DataFrame:
    """Load the state BMF named by `config` and return candidate organizations."""
    return filter_candidates(
        load_bmf(bmf_path(config)), config["state"], config["ntee_adjacent"]
    )
