"""Load project settings from config.yaml."""

from pathlib import Path
from typing import Any

import yaml


def load_config(path: Path = Path("config.yaml")) -> dict[str, Any]:
    """Read the YAML config at `path` and return it as a dict."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)
