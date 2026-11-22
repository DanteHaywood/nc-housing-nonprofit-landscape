"""Claude Code hook: format and lint a Python file right after Claude edits it.

Claude Code sends details of the edit as JSON on stdin. If the edited file is
a .py file, this runs `ruff format` and `ruff check --fix` on it. Any lint
errors Ruff can't fix are printed to stderr with exit code 2, which shows them
to Claude so it fixes them on its next step.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_ruff(project_dir: Path) -> str | None:
    """Prefer the project's virtual environment, then fall back to PATH."""
    for candidate in (
        project_dir / ".venv" / "bin" / "ruff",
        project_dir / ".venv" / "Scripts" / "ruff.exe",  # Windows
    ):
        if candidate.exists():
            return str(candidate)
    return shutil.which("ruff")


def main() -> int:
    event = json.load(sys.stdin)
    file_path = event.get("tool_input", {}).get("file_path", "")
    if not file_path.endswith(".py") or not Path(file_path).is_file():
        return 0

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    ruff = find_ruff(project_dir)
    if ruff is None:
        print('ruff not found; run `pip install -e ".[dev]"`', file=sys.stderr)
        return 1

    subprocess.run([ruff, "format", file_path], capture_output=True)
    check = subprocess.run(
        [ruff, "check", "--fix", file_path], capture_output=True, text=True
    )
    if check.returncode != 0:
        print(f"Ruff found issues it couldn't fix:\n{check.stdout}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
