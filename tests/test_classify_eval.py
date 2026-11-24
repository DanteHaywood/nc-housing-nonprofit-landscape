import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from evals.classify_eval import (
    NO_LABEL,
    NTEE_MAP_FILE,
    UNMAPPED,
    load_hand_labels,
    load_ntee_map,
    ntee_label,
    run,
    score,
)
from src.classify.classifier import (
    PREDICTION_COLUMNS,
    SHEET_ENCODING,
    prediction_file,
    write_predictions,
)

LABELS = [
    "emergency_shelter",
    "affordable_housing_development",
    "homeownership",
    "rental_assistance",
    "supportive_housing",
    "not_housing",
]


@pytest.fixture
def mapping() -> list[tuple[str, str]]:
    return load_ntee_map(NTEE_MAP_FILE, LABELS)


# The real map: longest prefix wins, vague group L codes are unmapped, and
# codes outside the map (or missing) are not_housing.
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("L21", "affordable_housing_development"),
        ("l22", "affordable_housing_development"),  # case doesn't matter
        ("L41", "emergency_shelter"),
        ("L4A", "emergency_shelter"),  # matched by L4
        ("L81", "homeownership"),
        ("L99", UNMAPPED),  # matched only by L
        ("L11", UNMAPPED),
        ("P73", "supportive_housing"),
        ("P60", "not_housing"),
        ("S31", "not_housing"),
        ("B20", "not_housing"),  # not in the map
        ("", "not_housing"),  # no NTEE code
        (None, "not_housing"),
    ],
)
def test_ntee_label(
    code: str | None, expected: str, mapping: list[tuple[str, str]]
) -> None:
    assert ntee_label(code, mapping) == expected


def test_load_ntee_map_rejects_unknown_label(tmp_path: Path) -> None:
    path = tmp_path / "map.csv"
    path.write_text("ntee_prefix,label,why\nL41,shelter,typo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the taxonomy: shelter"):
        load_ntee_map(path, LABELS)


# A small case worked by hand. Hand: 2 shelter, 1 rental, 1 not_housing.
# Predicted: shelter, rental, rental, unmapped.
def test_score() -> None:
    truth = pd.Series(
        ["emergency_shelter", "emergency_shelter", "rental_assistance", "not_housing"]
    )
    predicted = pd.Series(
        ["emergency_shelter", "rental_assistance", "rental_assistance", UNMAPPED]
    )
    s = score(truth, predicted, LABELS)
    assert (s["n"], s["correct"], s["accuracy"]) == (4, 2, 0.5)
    shelter = s["per_label"]["emergency_shelter"]
    assert (shelter["precision"], shelter["recall"]) == (1.0, 0.5)
    assert shelter["f1"] == round(2 / 3, 4)
    rental = s["per_label"]["rental_assistance"]
    assert (rental["precision"], rental["recall"]) == (0.5, 1.0)
    # never predicted and never in the hand labels: all undefined
    home = s["per_label"]["homeownership"]
    assert (home["precision"], home["recall"], home["f1"]) == (None, None, None)
    # in the hand labels but never predicted: recall 0, precision undefined
    nh = s["per_label"]["not_housing"]
    assert (nh["precision"], nh["recall"], nh["f1"]) == (None, 0.0, 0.0)
    assert s["confusion_columns"] == [*LABELS, UNMAPPED]
    assert s["confusion"]["emergency_shelter"]["rental_assistance"] == 1
    assert s["confusion"]["not_housing"][UNMAPPED] == 1


# Unlabeled rows stop the eval instead of being skipped.
def test_load_hand_labels_rejects_blank(tmp_path: Path) -> None:
    path = tmp_path / "labels_dev.csv"
    write_sheet(path, [("111111111", "")])
    with pytest.raises(ValueError, match="1 unlabeled rows"):
        load_hand_labels(path, LABELS)


def write_sheet(path: Path, rows: list[tuple[str, str]]) -> None:
    """Write a labeling sheet with the given (ein, hand label) rows."""
    df = pd.DataFrame(
        [
            {
                "ein": ein,
                "name": f"Org {ein}",
                "mission": "",
                "activity_or_mission": "",
                "programs": "",
                "schedule_o_text": "",
                "label": label,
                "notes": "",
            }
            for ein, label in rows
        ]
    )
    df.to_csv(path, index=False, encoding=SHEET_ENCODING)


def write_run(
    config: dict[str, Any], sheet: Path, classifier: str, rows: list[tuple]
) -> None:
    """Write a prediction CSV the way run_dev does."""
    version, model = ("v001", config["model"]) if classifier == "claude" else ("", "")
    df = pd.DataFrame(
        [
            (ein, f"Org {ein}", classifier, version, model, label, "why", status)
            for ein, label, status in rows
        ],
        columns=PREDICTION_COLUMNS,
    )
    write_predictions(df, prediction_file(config, sheet, classifier, version, model))


# End to end on a three-organization project: files are named for the split,
# prompt, and model, an api_error counts as wrong, and NTEE comes from DuckDB.
def test_run_end_to_end(tmp_path: Path) -> None:
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    config = {
        "model": "claude-test-model",
        "paths": {
            "labels": str(labels_dir),
            "processed": str(tmp_path / "processed" / "landscape.duckdb"),
        },
        "housing_labels": LABELS,
        "random_baseline_seed": 7,
    }
    sheet = labels_dir / "labels_dev.csv"
    write_sheet(
        sheet,
        [
            ("111111111", "emergency_shelter"),
            ("222222222", "homeownership"),
            ("333333333", "not_housing"),
        ],
    )
    pd.DataFrame(
        {
            "ein": ["111111111", "222222222", "333333333"],
            "sample_group": ["ntee_L", "ntee_L", "random"],
        }
    ).to_csv(labels_dir / "sample.csv", index=False)
    write_run(
        config,
        sheet,
        "claude",
        [
            ("111111111", "emergency_shelter", "ok"),
            ("222222222", "homeownership", "ok"),
            ("333333333", "", "api_error"),
        ],
    )
    write_run(
        config,
        sheet,
        "random_seed7",
        [(e, "not_housing", "ok") for e in ("111111111", "222222222", "333333333")],
    )
    db = Path(config["paths"]["processed"])
    db.parent.mkdir(exist_ok=True)  # the prediction CSVs already made it
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE SCHEMA ingest")
        con.execute(
            "CREATE TABLE ingest.organizations AS SELECT * FROM (VALUES "
            "('111111111', 'L41'), ('222222222', 'L99'), ('333333333', NULL)) "
            "t(ein, ntee_code)"
        )

    out = tmp_path / "results"
    results = run(config, "dev", "v001", out_dir=out)
    stem = "classify_dev__v001__claude-test-model"
    assert sorted(p.name for p in out.iterdir()) == [
        f"{stem}.csv",
        f"{stem}.json",
        f"{stem}.md",
    ]
    scores = results["scores"]
    assert scores["claude"]["correct"] == 2  # the api_error row is wrong
    assert scores["claude"]["confusion"]["not_housing"][NO_LABEL] == 1
    assert scores["ntee"]["correct"] == 2  # L41 and the missing code
    assert scores["ntee"]["confusion"]["homeownership"][UNMAPPED] == 1
    assert scores["random"]["correct"] == 1
    assert scores["claude"]["by_group"]["ntee_L"] == {
        "n": 2,
        "correct": 2,
        "accuracy": 1.0,
    }
    saved = json.loads((out / f"{stem}.json").read_text(encoding="utf-8"))
    assert saved["meta"]["model"] == "claude-test-model"
    table = pd.read_csv(out / f"{stem}.csv", dtype=str, keep_default_na=False)
    assert table["ntee"].tolist() == ["emergency_shelter", UNMAPPED, "not_housing"]
    assert "## claude: confusion matrix" in (out / f"{stem}.md").read_text(
        encoding="utf-8"
    )
