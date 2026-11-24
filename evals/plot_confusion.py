"""Draw Claude's and NTEE's confusion matrices side by side, as PNGs for the README.

    python -m evals.plot_confusion                      # dev, v001, model from config
    python -m evals.plot_confusion --results evals/results/classify_dev__v002__....json

Reads a saved eval result (evals/classify_eval.py) and writes a light and a
dark version to docs/images/, so README.md can show the one that matches the
reader's theme. Every number on the chart comes from that JSON file.

Rows are hand labels and columns are predictions. A cell's shade is its share of
the row (how a hand label's organizations were split), and its number is the
count. Empty cells are left blank so the diagonal and the mistakes stand out.
"""

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # files only, no window

import matplotlib.pyplot as plt  # noqa: E402  (must follow the backend choice)
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from evals.classify_eval import RESULTS_DIR, results_stem  # noqa: E402
from src.config import load_config  # noqa: E402

IMAGES_DIR = Path(__file__).parents[1] / "docs" / "images"

SHORT_NAMES = {
    "emergency_shelter": "Emergency\nshelter",
    "affordable_housing_development": "Affordable\ndevelopment",
    "homeownership": "Home-\nownership",
    "rental_assistance": "Rental\nassistance",
    "supportive_housing": "Supportive\nhousing",
    "not_housing": "Not\nhousing",
    "unmapped": "Unmapped\nNTEE code",
    "(no label)": "No label\n(error)",
}

# Blue sequential ramp (dataviz reference palette), near-zero end first. In dark
# mode the low end recedes toward the dark surface, so the ramp runs the other way.
BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "ink_secondary": "#52514e",
        "grid": "#e1e0d9",
        "ramp": BLUE,
        "dark_cell_from": 0.55,  # shares at or above this get light text
        "cell_text_on_dark": "#fcfcfb",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#f0efec",
        "ink_secondary": "#c3c2b7",
        "grid": "#2c2c2a",
        "ramp": BLUE[::-1][1:] + ["#e6f0fd"],
        "dark_cell_from": 0.55,  # light cells at the top of the ramp get dark text
        "cell_text_on_dark": "#0b0b0b",
    },
}


def one_line(name: str) -> str:
    """Join a two-line label onto one line: "Home-\\nownership" -> "Homeownership"."""
    return name.replace("-\n", "").replace("\n", " ")


def row_shares(confusion: dict[str, dict[str, int]], columns: list[str]) -> list:
    """Return each cell's share of its row (0 for an empty row)."""
    shares = []
    for row in confusion.values():
        total = sum(row[c] for c in columns)
        shares.append([row[c] / total if total else 0.0 for c in columns])
    return shares


def draw_panel(
    ax: plt.Axes,
    scores: dict[str, Any],
    title: str,
    rows: list[str],
    columns: list[str],
    theme: dict[str, Any],
) -> None:
    """Draw one confusion matrix as a heatmap with counts in non-empty cells."""
    cmap = LinearSegmentedColormap.from_list("seq", theme["ramp"])
    confusion = scores["confusion"]
    shares = row_shares(confusion, columns)
    gap = 0.04  # a thin surface-colored gap between cells
    for i, hand in enumerate(rows):
        for j, predicted in enumerate(columns):
            count = confusion[hand].get(predicted, 0)
            if count == 0:
                continue
            share = shares[i][j]
            ax.add_patch(
                plt.Rectangle(
                    (j + gap, i + gap),
                    1 - 2 * gap,
                    1 - 2 * gap,
                    facecolor=cmap(0.15 + 0.85 * share),
                    edgecolor="none",
                )
            )
            on_dark = share >= theme["dark_cell_from"]
            ax.text(
                j + 0.5,
                i + 0.5,
                str(count),
                ha="center",
                va="center",
                fontsize=13,
                fontweight="bold" if hand == predicted else "normal",
                color=theme["cell_text_on_dark"] if on_dark else theme["ink"],
            )
    # Outline the diagonal: those are the correct answers.
    for k, label in enumerate(rows):
        if label in columns:
            j = columns.index(label)
            ax.add_patch(
                plt.Rectangle(
                    (j + gap, k + gap),
                    1 - 2 * gap,
                    1 - 2 * gap,
                    facecolor="none",
                    edgecolor=theme["ink_secondary"],
                    linewidth=1.0,
                    linestyle=(0, (2, 2)),
                )
            )
    for row_number, hand in enumerate(rows):
        if sum(confusion[hand].values()) == 0:
            ax.text(
                len(columns) / 2,
                row_number + 0.5,
                "none in this set",
                ha="center",
                va="center",
                fontsize=9,
                style="italic",
                color=theme["ink_secondary"],
            )
    ax.set_xlim(0, len(columns))
    ax.set_ylim(len(rows), 0)
    ax.set_xticks([j + 0.5 for j in range(len(columns))])
    ax.set_xticklabels([SHORT_NAMES.get(c, c) for c in columns], fontsize=9)
    ax.set_yticks([i + 0.5 for i in range(len(rows))])
    ax.set_yticklabels([one_line(SHORT_NAMES.get(r, r)) for r in rows])
    ax.tick_params(length=0, colors=theme["ink_secondary"], labelsize=9)
    ax.xaxis.set_label_position("top")
    ax.xaxis.tick_top()
    ax.set_xlabel("Predicted label", color=theme["ink_secondary"], fontsize=10)
    ax.set_facecolor(theme["surface"])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(
        f"{title}\n{scores['correct']} of {scores['n']} correct "
        f"({100 * scores['accuracy']:.1f}%)",
        loc="left",
        fontsize=12,
        color=theme["ink"],
        pad=36,
    )


def plot(results: dict[str, Any], mode: str, out: Path) -> Path:
    """Draw the Claude and NTEE panels for one theme and save a PNG."""
    theme = THEMES[mode]
    meta, scores = results["meta"], results["scores"]
    rows = list(scores["claude"]["confusion"])
    # Shared columns, so the two panels line up cell for cell.
    columns = list(
        dict.fromkeys(
            scores["claude"]["confusion_columns"] + scores["ntee"]["confusion_columns"]
        )
    )
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.4), facecolor=theme["surface"])
    draw_panel(
        axes[0],
        scores["claude"],
        f"Claude ({meta['model']}, prompt {meta['prompt_version']})",
        rows,
        columns,
        theme,
    )
    draw_panel(
        axes[1], scores["ntee"], "IRS NTEE codes (baseline)", rows, columns, theme
    )
    axes[0].set_ylabel("Hand label", color=theme["ink_secondary"], fontsize=10)
    axes[1].set_yticklabels([])
    fig.suptitle(
        "Classifying North Carolina housing nonprofits from their IRS 990 filings",
        x=0.02,
        ha="left",
        fontsize=15,
        fontweight="bold",
        color=theme["ink"],
    )
    fig.text(
        0.02,
        0.915,
        f"{meta['n']} hand-labeled organizations ({meta['split']} set). Shade is the "
        "share of each hand label's row; numbers are counts; dashed cells are "
        "correct.",
        fontsize=10,
        color=theme["ink_secondary"],
    )
    stem = results_stem(meta["split"], meta["prompt_version"], meta["model"])
    fig.text(
        0.02,
        0.015,
        f"Source: evals/results/{stem}.json. "
        "NTEE codes mapped onto the taxonomy per D-018.",
        fontsize=8,
        color=theme["ink_secondary"],
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.93), w_pad=3)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180, facecolor=theme["surface"])
    plt.close(fig)
    return out


def macro_average(per_label: dict[str, dict[str, Any]], metric: str) -> float | None:
    """Mean of `metric` over labels that appear in the hand labels (support > 0).

    A label with no hand-labeled organizations can't be scored, so it's left
    out. A label that was present but never predicted has undefined precision,
    which counts as 0 here: the classifier found none of them.
    """
    values = [
        m[metric] if m[metric] is not None else 0.0
        for m in per_label.values()
        if m["support"] > 0
    ]
    return sum(values) / len(values) if values else None


def plot_precision_recall(results: dict[str, Any], mode: str, out: Path) -> Path:
    """Draw a table: one row per label, Claude's and NTEE's precision and recall.

    The last row is the macro average (`macro_average`). Cells are shaded by
    value on the same blue ramp as the confusion matrices; '–' is undefined.
    """
    theme = THEMES[mode]
    meta, scores = results["meta"], results["scores"]
    cmap = LinearSegmentedColormap.from_list("seq", theme["ramp"])
    labels = list(scores["claude"]["per_label"])
    classifiers = [("claude", "Claude"), ("ntee", "NTEE codes")]
    metrics = [("precision", "Precision"), ("recall", "Recall")]

    rows: list[tuple[str, Any, list[float | None]]] = []
    for label in labels:
        cells = [
            scores[c]["per_label"][label][m] for c, _ in classifiers for m, _ in metrics
        ]
        support = scores["claude"]["per_label"][label]["support"]
        rows.append((one_line(SHORT_NAMES[label]), support, cells))
    averages = [
        macro_average(scores[c]["per_label"], m)
        for c, _ in classifiers
        for m, _ in metrics
    ]
    n_scored = sum(m["support"] > 0 for m in scores["claude"]["per_label"].values())
    rows.append((f"Average ({n_scored} labels)", "", averages))

    # Column layout in data units: label, support, then four value cells.
    label_x, support_x = 0.0, 3.3
    value_x = [4.2, 5.45, 7.0, 8.25]  # left edges; Claude pair, gap, NTEE pair
    width, height = 1.15, 0.82
    fig, ax = plt.subplots(figsize=(10.5, 6.2), facecolor=theme["surface"])
    ax.set_facecolor(theme["surface"])
    ax.set_xlim(-0.1, value_x[-1] + width + 0.1)
    ax.set_ylim(len(rows) + 0.4, -2.1)
    ax.axis("off")
    ink, ink2 = theme["ink"], theme["ink_secondary"]

    # Two header rows: classifier names over their pairs, then metric names.
    for k, (_, name) in enumerate(classifiers):
        left, right = value_x[2 * k], value_x[2 * k + 1] + width
        ax.text(
            (left + right) / 2,
            -1.45,
            name,
            ha="center",
            va="center",
            fontsize=12,
            fontweight="bold",
            color=ink,
        )
        ax.plot([left + 0.05, right - 0.05], [-1.1, -1.1], color=theme["grid"], lw=1)
    for j, x in enumerate(value_x):
        ax.text(
            x + width / 2,
            -0.6,
            metrics[j % 2][1],
            ha="center",
            va="center",
            fontsize=10,
            color=ink2,
        )
    ax.text(
        label_x, -0.6, "Hand label", ha="left", va="center", fontsize=10, color=ink2
    )
    ax.text(support_x, -0.6, "Orgs", ha="center", va="center", fontsize=10, color=ink2)

    for i, (name, support, cells) in enumerate(rows):
        is_average = i == len(rows) - 1
        y = i + 0.5
        if is_average:
            ax.plot(
                [label_x, value_x[-1] + width], [i + 0.02, i + 0.02], color=ink2, lw=1.2
            )
        weight = "bold" if is_average else "normal"
        ax.text(
            label_x,
            y,
            name,
            ha="left",
            va="center",
            fontsize=11,
            color=ink,
            fontweight=weight,
        )
        ax.text(
            support_x,
            y,
            str(support),
            ha="center",
            va="center",
            fontsize=11,
            color=ink2,
        )
        for x, value in zip(value_x, cells, strict=True):
            if value is None:
                ax.text(
                    x + width / 2,
                    y,
                    "–",
                    ha="center",
                    va="center",
                    fontsize=11,
                    color=ink2,
                )
                continue
            ax.add_patch(
                plt.Rectangle(
                    (x, y - height / 2),
                    width,
                    height,
                    facecolor=cmap(0.1 + 0.9 * value),
                    edgecolor="none",
                )
            )
            on_dark = 0.1 + 0.9 * value >= theme["dark_cell_from"]
            ax.text(
                x + width / 2,
                y,
                f"{value:.2f}",
                ha="center",
                va="center",
                fontsize=11,
                fontweight=weight,
                color=theme["cell_text_on_dark"] if on_dark else ink,
            )

    fig.suptitle(
        "Precision and recall by label: Claude vs. IRS NTEE codes",
        x=0.03,
        ha="left",
        fontsize=15,
        fontweight="bold",
        color=ink,
    )
    fig.text(
        0.03,
        0.885,
        f"{meta['n']} hand-labeled organizations ({meta['split']} set), Claude "
        f"{meta['model']} with prompt {meta['prompt_version']}.\n"
        "Precision: of the organizations given a label, the share that had it. "
        "Recall: of those that had it, the share found.\n"
        "– means undefined (never predicted, or no hand-labeled organizations). "
        "The average leaves out labels with none in the set\n"
        "and counts undefined precision as 0.",
        fontsize=9.5,
        color=ink2,
        va="top",
        linespacing=1.5,
    )
    stem = results_stem(meta["split"], meta["prompt_version"], meta["model"])
    fig.text(0.03, 0.02, f"Source: evals/results/{stem}.json", fontsize=8, color=ink2)
    fig.subplots_adjust(left=0.03, right=0.97, top=0.75, bottom=0.07)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180, facecolor=theme["surface"])
    plt.close(fig)
    return out


def main() -> None:
    config = load_config()
    default = RESULTS_DIR / f"{results_stem('dev', 'v001', config['model'])}.json"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=default)
    args = parser.parse_args()
    results = json.loads(args.results.read_text(encoding="utf-8"))
    meta = results["meta"]
    suffix = f"{meta['split']}_{meta['prompt_version']}"
    for mode in THEMES:
        name = f"confusion_{suffix}_{mode}.png"
        print(f"Wrote {plot(results, mode, IMAGES_DIR / name)}")
        name = f"precision_recall_{suffix}_{mode}.png"
        print(f"Wrote {plot_precision_recall(results, mode, IMAGES_DIR / name)}")


if __name__ == "__main__":
    main()
