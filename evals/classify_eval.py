"""Stage 1 eval: score Claude, the NTEE baseline, and random labels against hand labels.

    python evals/classify_eval.py                 # dev sheet, prompt v001
    python evals/classify_eval.py --prompt v002
    python evals/classify_eval.py --split test    # only once a prompt version is final

Reads the hand labels from evals/labels/labels_{split}.csv, the predictions
that src/classify/run_dev.py wrote to data/processed/, NTEE codes from the
`ingest` schema of the DuckDB file, and sample groups from
evals/labels/sample.csv. NTEE codes are mapped onto the taxonomy with
evals/ntee_taxonomy_map.csv (D-018).

For each classifier it reports accuracy (overall and by sample group),
per-label precision, recall, and F1, and a confusion matrix. Results go to
evals/results/classify_{split}__{prompt_version}__{model}.{json,md,csv}. The
CSV has one row per organization, for the error analysis. Numbers in README.md
come only from these files.
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd

from src.classify.classifier import (
    SHEET_ENCODING,
    STATUS_OK,
    eval_set_path,
    load_sheet,
    prediction_file,
)
from src.config import load_config

logger = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).parent
NTEE_MAP_FILE = EVALS_DIR / "ntee_taxonomy_map.csv"
RESULTS_DIR = EVALS_DIR / "results"
SAMPLE_FILE = "sample.csv"

UNMAPPED = "unmapped"  # an NTEE code that says housing but not which kind
NO_LABEL = "(no label)"  # a Claude row whose status isn't ok
DEFAULT_LABEL = "not_housing"  # codes outside the map, and missing codes
CLASSIFIERS = ["claude", "ntee", "random"]


# --- NTEE baseline -----------------------------------------------------------


def load_ntee_map(path: Path, labels: list[str]) -> list[tuple[str, str]]:
    """Read the NTEE map as (prefix, label) pairs, longest prefix first.

    Raises ValueError for a label outside the taxonomy (other than `unmapped`)
    or a repeated prefix.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df["ntee_prefix"] = df["ntee_prefix"].str.strip().str.upper()
    bad = sorted(set(df["label"]) - set(labels) - {UNMAPPED})
    if bad:
        raise ValueError(f"{path}: labels outside the taxonomy: {', '.join(bad)}")
    repeated = df.loc[df["ntee_prefix"].duplicated(), "ntee_prefix"]
    if not repeated.empty:
        raise ValueError(f"{path}: prefix {repeated.iloc[0]} appears twice")
    pairs = list(zip(df["ntee_prefix"], df["label"], strict=True))
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


def ntee_label(code: str | None, mapping: list[tuple[str, str]]) -> str:
    """Map one NTEE code to a label by its longest matching prefix.

    A missing code, or one no prefix matches, is `not_housing`: NTEE doesn't
    call it housing.
    """
    code = (code or "").strip().upper()
    if not code:
        return DEFAULT_LABEL
    for prefix, label in mapping:
        if code.startswith(prefix):
            return label
    return DEFAULT_LABEL


def map_hash(path: Path) -> str:
    """Return a short hash of the NTEE map file, saved with the results."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# --- Inputs ------------------------------------------------------------------


def load_hand_labels(sheet_path: Path, labels: list[str]) -> pd.DataFrame:
    """Return ein, name, and hand_label from a labeled sheet.

    Raises ValueError if any row is unlabeled or uses a label outside the
    taxonomy. Hand labels are never fixed up here (CLAUDE.md eval rules).
    """
    sheet = load_sheet(sheet_path)
    sheet["hand_label"] = sheet["label"].str.strip()
    blank = sheet.loc[sheet["hand_label"] == "", "ein"]
    if not blank.empty:
        raise ValueError(f"{len(blank)} unlabeled rows in {sheet_path}")
    bad = sorted(set(sheet["hand_label"]) - set(labels))
    if bad:
        raise ValueError(f"{sheet_path}: labels outside the taxonomy: {bad}")
    return sheet[["ein", "name", "hand_label"]]


def load_predictions(path: Path, eins: pd.Series) -> pd.DataFrame:
    """Return ein, predicted, status, and reason for every EIN in `eins`.

    Rows whose status isn't ok predict `(no label)`, so they count as wrong.
    Raises FileNotFoundError or ValueError if predictions are missing.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"No predictions at {path}. Run python -m src.classify.run_dev first."
        )
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=SHEET_ENCODING)
    missing = sorted(set(eins) - set(df["ein"]))
    if missing:
        raise ValueError(f"{path} has no prediction for {len(missing)} EINs")
    df.loc[df["status"] != STATUS_OK, "predicted"] = NO_LABEL
    return df.set_index("ein").loc[list(eins), ["predicted", "status", "reason"]]


def load_ntee_codes(database: Path, eins: list[str]) -> pd.DataFrame:
    """Return ein and ntee_code from the BMF, read-only. Missing codes are ''."""
    with duckdb.connect(str(database), read_only=True) as con:
        df = con.execute(
            "SELECT ein, coalesce(ntee_code, '') AS ntee_code "
            "FROM ingest.organizations WHERE ein IN (SELECT unnest(?))",
            [eins],
        ).df()
    absent = sorted(set(eins) - set(df["ein"]))
    if absent:
        raise ValueError(f"{len(absent)} sheet EINs are not in ingest.organizations")
    return df


def load_sample_groups(labels_dir: Path) -> pd.DataFrame:
    """Return ein and sample_group from evals/labels/sample.csv (D-015)."""
    df = pd.read_csv(labels_dir / SAMPLE_FILE, dtype=str, keep_default_na=False)
    return df[["ein", "sample_group"]]


# --- Scoring -----------------------------------------------------------------


def evaluation_table(
    hand: pd.DataFrame,
    claude: pd.DataFrame,
    random_labels: pd.DataFrame,
    ntee_codes: pd.DataFrame,
    groups: pd.DataFrame,
    mapping: list[tuple[str, str]],
) -> pd.DataFrame:
    """Join everything into one row per organization.

    Columns: ein, name, sample_group, hand_label, claude, claude_status,
    claude_reason, ntee_code, ntee, random.
    """
    table = hand.merge(groups, on="ein", how="left", validate="one_to_one")
    table["sample_group"] = table["sample_group"].fillna("")
    table = table.merge(ntee_codes, on="ein", how="left", validate="one_to_one")
    table["ntee_code"] = table["ntee_code"].fillna("")
    table["ntee"] = [ntee_label(code, mapping) for code in table["ntee_code"]]
    eins = table["ein"]
    table["claude"] = claude.loc[eins, "predicted"].to_numpy()
    table["claude_status"] = claude.loc[eins, "status"].to_numpy()
    table["claude_reason"] = claude.loc[eins, "reason"].to_numpy()
    table["random"] = random_labels.loc[eins, "predicted"].to_numpy()
    columns = ["ein", "name", "sample_group", "hand_label", "claude"]
    columns += ["claude_status", "claude_reason", "ntee_code", "ntee", "random"]
    return table[columns]


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score(truth: pd.Series, predicted: pd.Series, labels: list[str]) -> dict[str, Any]:
    """Return accuracy, per-label precision/recall/F1, and a confusion matrix.

    Precision is None for a label never predicted, recall for one never in the
    hand labels, and F1 for a label that is neither. The confusion matrix has
    hand labels as rows and predictions as columns; predictions outside
    `labels` (unmapped, no label) get their own columns.
    """
    truth, predicted = truth.reset_index(drop=True), predicted.reset_index(drop=True)
    correct = int((truth == predicted).sum())
    per_label = {}
    for label in labels:
        tp = int(((truth == label) & (predicted == label)).sum())
        n_predicted = int((predicted == label).sum())
        support = int((truth == label).sum())
        precision, recall = _ratio(tp, n_predicted), _ratio(tp, support)
        # F1 from counts: 2TP / (predicted + support). None if both are zero.
        f1 = _ratio(2 * tp, n_predicted + support)
        per_label[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
            "predicted": n_predicted,
        }
    extra = sorted(set(predicted) - set(labels))
    columns = labels + extra
    confusion = {
        hand_label: {
            column: int(((truth == hand_label) & (predicted == column)).sum())
            for column in columns
        }
        for hand_label in labels
    }
    return {
        "n": len(truth),
        "correct": correct,
        "accuracy": _ratio(correct, len(truth)),
        "per_label": per_label,
        "confusion_columns": columns,
        "confusion": confusion,
    }


def accuracy_by_group(table: pd.DataFrame, classifier: str) -> dict[str, Any]:
    """Return n, correct, and accuracy per sample group (D-015 asks for this)."""
    out = {}
    for group, rows in table.groupby("sample_group", sort=True):
        correct = int((rows["hand_label"] == rows[classifier]).sum())
        out[group or "(none)"] = {
            "n": len(rows),
            "correct": correct,
            "accuracy": _ratio(correct, len(rows)),
        }
    return out


def evaluate(table: pd.DataFrame, labels: list[str]) -> dict[str, Any]:
    """Score every classifier in `CLASSIFIERS` on the evaluation table."""
    return {
        name: {
            **score(table["hand_label"], table[name], labels),
            "by_group": accuracy_by_group(table, name),
        }
        for name in CLASSIFIERS
    }


# --- Report ------------------------------------------------------------------


def results_stem(split: str, prompt_version: str, model: str) -> str:
    """Return e.g. classify_dev__v001__claude-haiku-5-5."""
    return f"classify_{split}__{prompt_version}__{model}"


def _pct(value: float | None) -> str:
    """Format an accuracy as a percentage with one decimal, e.g. 82.5%."""
    return "–" if value is None else f"{100 * value:.1f}%"


def _md_table(header: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return "–"
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)

    lines = ["| " + " | ".join(header) + " |"]
    lines.append("|" + "|".join("---" for _ in header) + "|")
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def markdown_report(results: dict[str, Any]) -> str:
    """Render the results dict as a Markdown report."""
    meta, scores = results["meta"], results["scores"]
    out = [
        f"# Stage 1 eval: {meta['split']} set, prompt {meta['prompt_version']}, "
        f"{meta['model']}",
        "",
        f"Generated {meta['generated_at']} from {meta['n']} hand-labeled "
        "organizations. The labels are strict on primary purpose (D-017). "
        f"NTEE codes are mapped with `{meta['ntee_map_file']}` (D-018); "
        "`unmapped` means a group L code that names no kind of housing, scored "
        f"wrong. Claude rows without an ok status count as `{NO_LABEL}`.",
        "",
        "## Accuracy",
        "",
        _md_table(
            ["Classifier", "Correct", "Accuracy"],
            [[n, s["correct"], _pct(s["accuracy"])] for n, s in scores.items()],
        ),
        "",
        "## Accuracy by sample group",
        "",
    ]
    groups = sorted({g for s in scores.values() for g in s["by_group"]})
    rows = []
    for group in groups:
        row: list[Any] = [group, scores["claude"]["by_group"][group]["n"]]
        row += [_pct(scores[n]["by_group"][group]["accuracy"]) for n in scores]
        rows.append(row)
    out += [_md_table(["Group", "n", *scores], rows), ""]
    for name, s in scores.items():
        out += [f"## {name}: per-label precision and recall", ""]
        out.append(
            _md_table(
                ["Label", "Support", "Predicted", "Precision", "Recall", "F1"],
                [
                    [label, m["support"], m["predicted"]]
                    + [m["precision"], m["recall"], m["f1"]]
                    for label, m in s["per_label"].items()
                ],
            )
        )
        out += ["", f"## {name}: confusion matrix (rows: hand label)", ""]
        columns = s["confusion_columns"]
        out.append(
            _md_table(
                ["hand \\ predicted", *columns],
                [[h, *(s["confusion"][h][c] for c in columns)] for h in s["confusion"]],
            )
        )
        out.append("")
    return "\n".join(out)


def write_results(
    results: dict[str, Any], table: pd.DataFrame, out_dir: Path, stem: str
) -> list[Path]:
    """Write {stem}.json, {stem}.md, and {stem}.csv to `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / f"{stem}.{ext}" for ext in ("json", "md", "csv")]
    paths[0].write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    paths[1].write_text(markdown_report(results), encoding="utf-8")
    table.to_csv(paths[2], index=False, encoding=SHEET_ENCODING)
    return paths


# --- CLI ---------------------------------------------------------------------


def run(
    config: dict[str, Any],
    split: Literal["dev", "test"] = "dev",
    prompt_version: str = "v001",
    out_dir: Path = RESULTS_DIR,
    ntee_map_path: Path = NTEE_MAP_FILE,
) -> dict[str, Any]:
    """Load everything, score it, write the results, and return them."""
    labels = list(config["housing_labels"])
    model = config["model"]
    sheet_path = eval_set_path(config, split)
    hand = load_hand_labels(sheet_path, labels)
    claude = load_predictions(
        prediction_file(config, sheet_path, "claude", prompt_version, model),
        hand["ein"],
    )
    seed = config["random_baseline_seed"]
    random_labels = load_predictions(
        prediction_file(config, sheet_path, f"random_seed{seed}"), hand["ein"]
    )
    mapping = load_ntee_map(ntee_map_path, labels)
    table = evaluation_table(
        hand,
        claude,
        random_labels,
        load_ntee_codes(Path(config["paths"]["processed"]), hand["ein"].tolist()),
        load_sample_groups(Path(config["paths"]["labels"])),
        mapping,
    )
    results = {
        "meta": {
            "split": split,
            "prompt_version": prompt_version,
            "model": model,
            "n": len(table),
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "random_seed": seed,
            "ntee_map_file": ntee_map_path.name,
            "ntee_map_sha256": map_hash(ntee_map_path),
        },
        "scores": evaluate(table, labels),
    }
    stem = results_stem(split, prompt_version, model)
    for path in write_results(results, table, out_dir, stem):
        logger.info("Wrote %s", path)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--prompt", default="v001", help="prompt version")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.split == "test":
        logger.warning(
            "Scoring the test set. Do this once per final prompt version, and "
            "don't tune the prompt on what you see."
        )
    try:
        results = run(load_config(), args.split, args.prompt)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    for name, s in results["scores"].items():
        print(f"{name:>7}: {s['correct']}/{s['n']} correct ({_pct(s['accuracy'])})")


if __name__ == "__main__":
    main()
