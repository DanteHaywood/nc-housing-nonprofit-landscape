"""A Claude classifier and a random baseline for labeling-sheet CSVs.

Input is any CSV laid out like `evals/labels/labels_dev.csv` (D-015): one row
per organization, with its filing text in the `SHEET_TEXT_COLUMNS`. Each
classifier returns one prediction per row, which `write_predictions` saves as
a CSV next to the DuckDB file in `paths.processed`.

The Claude classifier fills a versioned prompt (`prompts/vNNN.md`, D-016) and
forces one strict tool call, `record_label`, whose input is a one-sentence
reason and a label from `config.yaml: housing_labels`. Every response is cached
as JSONL in `data/interim/api_cache/`, keyed by EIN, prompt version, and model,
so reruns are free and reproducible unless `refresh=True`.
"""

import hashlib
import json
import logging
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import anthropic
import pandas as pd

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SHEET_ENCODING = "utf-8-sig"  # the sheets are written with a BOM for Excel
SHEET_TEXT_COLUMNS = [
    "name",
    "mission",
    "activity_or_mission",
    "programs",
    "schedule_o_text",
]
TOOL_NAME = "record_label"
MAX_TOKENS = 1024

# One row per organization per classifier run.
PREDICTION_COLUMNS = [
    "ein",
    "name",
    "classifier",
    "prompt_version",
    "model",
    "predicted",
    "reason",
    "status",
]
# Why a prediction is or isn't usable. Only "ok" rows count as a label.
STATUS_OK = "ok"
STATUS_INVALID = "invalid_output"  # label outside the taxonomy, or no reason
STATUS_NO_TOOL = "no_tool_call"
STATUS_REFUSAL = "refusal"
STATUS_API_ERROR = "api_error"  # never cached, so a rerun tries again


# --- Input sheets ------------------------------------------------------------


def eval_set_path(config: dict[str, Any], split: Literal["dev", "test"]) -> Path:
    """Return the labeling sheet for a split, e.g. evals/labels/labels_dev.csv."""
    return Path(config["paths"]["labels"]) / f"labels_{split}.csv"


def load_sheet(path: Path) -> pd.DataFrame:
    """Read a labeling-sheet CSV with every column as a string.

    The sheet needs an `ein` column and the `SHEET_TEXT_COLUMNS`; other columns
    (`label`, `notes`, ...) are kept. EINs are zero-padded to 9 digits.
    Raises ValueError on missing columns, malformed EINs, or repeated EINs.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=SHEET_ENCODING)
    missing = [c for c in ["ein", *SHEET_TEXT_COLUMNS] if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing columns: {', '.join(missing)}")
    df["ein"] = df["ein"].str.strip().str.zfill(9)
    bad = df.loc[~df["ein"].str.fullmatch(r"\d{9}"), "ein"]
    if not bad.empty:
        raise ValueError(f"{len(bad)} malformed EINs in {path}, e.g. {bad.iloc[0]!r}")
    repeated = df.loc[df["ein"].duplicated(), "ein"]
    if not repeated.empty:
        raise ValueError(f"EIN {repeated.iloc[0]} appears more than once in {path}")
    logger.info("Loaded %d organizations from %s", len(df), path)
    return df


# --- Prompt and tool ---------------------------------------------------------


def load_prompt(version: str) -> str:
    """Return the text of prompts/{version}.md, e.g. version 'v001'."""
    path = PROMPTS_DIR / f"{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"No prompt version {version!r} at {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, row: pd.Series | dict[str, str]) -> str:
    """Fill each {{column}} placeholder with that column's text from `row`.

    Empty fields become empty tags; the prompt tells the model some may be
    empty. Raises ValueError if a placeholder is left unfilled.
    """
    text = template
    for column in SHEET_TEXT_COLUMNS:
        text = text.replace("{{" + column + "}}", str(row[column]).strip())
    if "{{" in text:
        start = text.index("{{")
        raise ValueError(f"Unfilled placeholder in prompt: {text[start : start + 40]}")
    return text


def label_tool(labels: list[str]) -> dict[str, Any]:
    """Return the strict `record_label` tool: a reason, then one of `labels`."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Record the organization's one primary housing label and the reason for it."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "One sentence citing what in the text decided it.",
                },
                "label": {"type": "string", "enum": list(labels)},
            },
            "required": ["reason", "label"],
            "additionalProperties": False,
        },
    }


def request_params(model: str, prompt: str, labels: list[str]) -> dict[str, Any]:
    """Return the `messages.create` arguments for one organization."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "tools": [label_tool(labels)],
        # Haiku 5.5 allows forcing a named tool; the call then skips thinking.
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "messages": [{"role": "user", "content": prompt}],
    }


def parse_response(response: dict[str, Any], labels: list[str]) -> tuple[str, str, str]:
    """Return (predicted, reason, status) from a response dict.

    Works on `Message.to_dict()` output, so fresh and cached responses are
    parsed the same way. A label outside `labels` is returned as written with
    status `invalid_output`, never coerced to the nearest label.
    """
    if response.get("stop_reason") == "refusal":
        return "", "", STATUS_REFUSAL
    calls = [
        block
        for block in response.get("content", [])
        if block.get("type") == "tool_use" and block.get("name") == TOOL_NAME
    ]
    if not calls:
        return "", "", STATUS_NO_TOOL
    tool_input = calls[0].get("input") or {}
    label = str(tool_input.get("label", "")).strip()
    reason = str(tool_input.get("reason", "")).strip()
    if label not in labels or not reason:
        return label, reason, STATUS_INVALID
    return label, reason, STATUS_OK


# --- Cache -------------------------------------------------------------------


def cache_path(config: dict[str, Any], prompt_version: str, model: str) -> Path:
    """Return the JSONL cache file for one prompt version and model."""
    return (
        Path(config["paths"]["interim"])
        / "api_cache"
        / f"{prompt_version}__{model}.jsonl"
    )


def read_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Return cached records by EIN. A later line for an EIN wins (--refresh)."""
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = json.loads(line)
                records[record["ein"]] = record
    return records


def append_cache(path: Path, record: dict[str, Any]) -> None:
    """Append one record to the JSONL cache, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def prompt_hash(prompt: str) -> str:
    """Return a short hash of the filled prompt, to spot changed input text."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def uncached_eins(
    sheet: pd.DataFrame, config: dict[str, Any], prompt_version: str
) -> list[str]:
    """Return the sheet's EINs that have no cached response yet."""
    cached = read_cache(cache_path(config, prompt_version, config["model"]))
    return [ein for ein in sheet["ein"] if ein not in cached]


# --- Classifiers -------------------------------------------------------------


def classify_claude(
    sheet: pd.DataFrame,
    config: dict[str, Any],
    prompt_version: str,
    client: anthropic.Anthropic | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Label each sheet row with Claude and return `PREDICTION_COLUMNS`.

    Responses come from the cache unless missing or `refresh` is set. The
    client is created only if a call is needed, and reads ANTHROPIC_API_KEY
    from the environment. API errors are logged, given status `api_error`, and
    not cached.
    """
    model = config["model"]
    labels = list(config["housing_labels"])
    template = load_prompt(prompt_version)
    path = cache_path(config, prompt_version, model)
    cache = {} if refresh else read_cache(path)

    rows, calls, from_cache = [], 0, 0
    for _, org in sheet.iterrows():
        ein = org["ein"]
        prompt = render_prompt(template, org)
        record = cache.get(ein)
        if record is not None:
            if record.get("prompt_sha256") != prompt_hash(prompt):
                logger.warning(
                    "EIN %s: cached response was made from different filing text; "
                    "using it anyway (rerun with --refresh to replace it)",
                    ein,
                )
            response = record["response"]
            from_cache += 1
        else:
            if client is None:
                client = anthropic.Anthropic()
            try:
                message = client.messages.create(
                    **request_params(model, prompt, labels)
                )
            except anthropic.APIError as e:
                logger.error("EIN %s: API error: %s", ein, e)
                rows.append(_prediction(org, "claude", prompt_version, model))
                rows[-1]["status"] = STATUS_API_ERROR
                continue
            calls += 1
            response = message.to_dict()
            append_cache(
                path,
                {
                    "ein": ein,
                    "prompt_version": prompt_version,
                    "model": model,
                    "prompt_sha256": prompt_hash(prompt),
                    "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "response": response,
                },
            )
        predicted, reason, status = parse_response(response, labels)
        if status != STATUS_OK:
            logger.error("EIN %s: %s (label %r)", ein, status, predicted)
        row = _prediction(org, "claude", prompt_version, model)
        row.update(predicted=predicted, reason=reason, status=status)
        rows.append(row)

    logger.info(
        "Claude %s on %s: %d organizations, %d API calls, %d from cache",
        prompt_version,
        model,
        len(sheet),
        calls,
        from_cache,
    )
    return pd.DataFrame(rows, columns=PREDICTION_COLUMNS)


def classify_random(sheet: pd.DataFrame, labels: list[str], seed: int) -> pd.DataFrame:
    """Give each sheet row a label drawn uniformly from `labels`.

    The same seed and sheet order give the same labels. This is the floor a
    real classifier has to clear.
    """
    rng = random.Random(seed)
    rows = []
    for _, org in sheet.iterrows():
        row = _prediction(org, f"random_seed{seed}", "", "")
        row.update(predicted=rng.choice(labels), reason="", status=STATUS_OK)
        rows.append(row)
    return pd.DataFrame(rows, columns=PREDICTION_COLUMNS)


def _prediction(
    org: pd.Series, classifier: str, prompt_version: str, model: str
) -> dict[str, str]:
    """Return an empty prediction row for one organization."""
    return {
        "ein": org["ein"],
        "name": org["name"],
        "classifier": classifier,
        "prompt_version": prompt_version,
        "model": model,
        "predicted": "",
        "reason": "",
        "status": "",
    }


# --- Output ------------------------------------------------------------------


def predictions_dir(config: dict[str, Any]) -> Path:
    """Return the folder that holds the DuckDB file (data/processed/)."""
    return Path(config["paths"]["processed"]).parent


def prediction_path(
    config: dict[str, Any], sheet_path: Path, predictions: pd.DataFrame
) -> Path:
    """Name the output after the sheet and classifier.

    For example data/processed/labels_dev__claude__v001__claude-haiku-5-5.csv
    or data/processed/labels_dev__random_seed7.csv.
    """
    first = predictions.iloc[0] if len(predictions) else None
    parts = [sheet_path.stem]
    if first is not None:
        parts += [
            p
            for p in (first["classifier"], first["prompt_version"], first["model"])
            if p
        ]
    return predictions_dir(config) / ("__".join(parts) + ".csv")


def write_predictions(predictions: pd.DataFrame, path: Path) -> Path:
    """Write predictions as UTF-8 CSV (with a BOM, like the sheets) and return the path.

    Output is derived from the cache, so it may be overwritten on rerun.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(path, index=False, encoding=SHEET_ENCODING)
    logger.info("Wrote %d predictions to %s", len(predictions), path)
    return path


def status_counts(predictions: pd.DataFrame) -> dict[str, int]:
    """Return how many predictions have each status."""
    return predictions["status"].value_counts().to_dict()
