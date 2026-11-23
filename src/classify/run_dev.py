"""Run the Claude classifier and the random baseline on the dev sheet.

    python -m src.classify.run_dev               # cached responses, plus any new ones
    python -m src.classify.run_dev --yes         # allow more than 20 new API calls
    python -m src.classify.run_dev --refresh     # ignore the cache and call again
    python -m src.classify.run_dev --prompt v002

Reads evals/labels/labels_dev.csv and writes one prediction CSV per classifier
to the folder of `paths.processed`. Before calling the API it says how many
calls it will make, and it stops if that's over 20 unless --yes is passed
(CLAUDE.md). ANTHROPIC_API_KEY comes from the shell or from .env in the repo
root (gitignored). Scoring against the hand labels is evals/classify_eval.py's job.
"""

import argparse
import logging
import sys
from typing import Any

from src.classify.classifier import (
    classify_claude,
    classify_random,
    eval_set_path,
    load_sheet,
    prediction_path,
    status_counts,
    uncached_eins,
    write_predictions,
)
from src.config import load_config, load_env

DEFAULT_PROMPT = "v001"
CALL_LIMIT = 20  # more new calls than this needs --yes


def calls_allowed(new_calls: int, yes: bool, limit: int = CALL_LIMIT) -> bool:
    """Return True if `new_calls` API calls may run without asking first."""
    return new_calls <= limit or yes


def run(
    config: dict[str, Any],
    prompt_version: str = DEFAULT_PROMPT,
    refresh: bool = False,
    yes: bool = False,
) -> int:
    """Classify the dev sheet both ways and write the CSVs. Returns an exit code."""
    sheet_path = eval_set_path(config, "dev")
    sheet = load_sheet(sheet_path)

    new_calls = (
        len(sheet) if refresh else len(uncached_eins(sheet, config, prompt_version))
    )
    print(
        f"Dev sheet: {len(sheet)} organizations. Prompt {prompt_version} on "
        f"{config['model']} needs {new_calls} new API calls "
        f"({len(sheet) - new_calls} cached)."
    )
    if not calls_allowed(new_calls, yes):
        print(
            f"That's more than {CALL_LIMIT} calls. Rerun with --yes to make them.",
            file=sys.stderr,
        )
        return 1

    outputs = {
        "random": classify_random(
            sheet, list(config["housing_labels"]), config["random_baseline_seed"]
        ),
        "claude": classify_claude(sheet, config, prompt_version, refresh=refresh),
    }
    for name, predictions in outputs.items():
        path = write_predictions(
            predictions, prediction_path(config, sheet_path, predictions)
        )
        print(f"{name}: {status_counts(predictions)} -> {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="prompt version")
    parser.add_argument("--refresh", action="store_true", help="bypass the cache")
    parser.add_argument(
        "--yes", action="store_true", help=f"allow more than {CALL_LIMIT} calls"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_env()  # ANTHROPIC_API_KEY from .env, unless already set in the shell
    sys.exit(run(load_config(), args.prompt, args.refresh, args.yes))


if __name__ == "__main__":
    main()
