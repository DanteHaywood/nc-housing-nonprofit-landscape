"""Load project settings from config.yaml, and secrets from .env."""

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def load_config(path: Path = Path("config.yaml")) -> dict[str, Any]:
    """Read the YAML config at `path` and return it as a dict."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_env(path: Path = Path(".env")) -> bool:
    """Copy variables such as ANTHROPIC_API_KEY from `path` into the environment.

    A variable already set in the shell wins over the file. Returns True if the
    file existed. Call this from CLI entry points only; library code reads
    os.environ and never the file.
    """
    if not path.exists():
        return False
    load_dotenv(path, override=False)
    return True
