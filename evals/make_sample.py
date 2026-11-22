"""Draw the eval's labeled sample and write blank labeling sheets (D-015).

Two steps, with an ingest rebuild between them, because the random group's
filings aren't in the database until their EINs are sampled:

    python evals/make_sample.py draw      # -> evals/labels/sample.csv
    python -m src.ingest.build            # ingests the sampled EINs too
    python evals/make_sample.py sheet     # -> labels_dev.csv, labels_test.csv

**draw** samples three groups (D-009) from DuckDB with a fixed seed
(`config.yaml: label_sample`):
- `ntee_L`: organizations in NTEE major group L with a parsed filing
- `ntee_adjacent`: organizations in the adjacent codes with a parsed filing
- `random`: non-candidate organizations with a 990 or 990-EZ in the index,
  plus ranked reserves in case one fails to ingest

**sheet** takes each group's first organizations (in rank order) that now have a
parsed filing, splits them into dev and test in the same proportions, and
writes blank labeling sheets. The sheets are blind: no NTEE code and no sample
group, so hand labels aren't anchored on the baseline they're scored against.

Neither step overwrites an existing file. The sample and the hand labels are
frozen once written. Changing them takes a deliberate delete and a decision
entry (CLAUDE.md eval rules).
"""

import argparse
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.config import load_config
from src.ingest.build import LABEL_SAMPLE_FILE, database_path

logger = logging.getLogger(__name__)

GROUPS = ("ntee_L", "ntee_adjacent", "random")
# The frame each group is drawn from. All three require text to label: a
# parsed filing now (L, adjacent), or a 990/990-EZ that ingest can fetch (random).
FRAME_SQL = {
    "ntee_L": """
        SELECT ein FROM ingest.organizations
        WHERE candidate_group = 'ntee_L'
          AND ein IN (SELECT ein FROM ingest.filings)""",
    "ntee_adjacent": """
        SELECT ein FROM ingest.organizations
        WHERE candidate_group = 'ntee_adjacent'
          AND ein IN (SELECT ein FROM ingest.filings)""",
    "random": """
        SELECT DISTINCT o.ein FROM ingest.organizations o
        JOIN ingest.state_index s USING (ein)
        WHERE o.candidate_group IS NULL AND s.return_type IN ('990', '990EZ')""",
}
SAMPLE_COLUMNS = ["ein", "sample_group", "rank", "role", "seed", "drawn_on"]
SHEET_FILES = {"dev": "labels_dev.csv", "test": "labels_test.csv"}
SHEET_COLUMNS = [
    "ein",
    "name",
    "tax_period",
    "return_type",
    "object_id",
    "mission",
    "activity_or_mission",
    "programs",
    "schedule_o_text",
    "label",
    "notes",
]
# Excel opens UTF-8 CSVs correctly only with a byte-order mark.
SHEET_ENCODING = "utf-8-sig"


@dataclass(frozen=True)
class SampleSettings:
    """`config.yaml: label_sample`, checked."""

    seed: int
    groups: dict[str, int]
    random_reserves: int
    dev_size: int

    @property
    def size(self) -> int:
        return sum(self.groups.values())


def sample_settings(config: dict[str, Any]) -> SampleSettings:
    """Read and check the sample settings in `config`.

    Raises ValueError if the groups aren't exactly GROUPS, don't add up to
    `label_sample_size`, or the dev set isn't smaller than the whole sample.
    """
    raw = config["label_sample"]
    settings = SampleSettings(
        seed=int(raw["seed"]),
        groups={group: int(raw["groups"][group]) for group in raw["groups"]},
        random_reserves=int(raw.get("random_reserves", 0)),
        dev_size=int(raw["dev_size"]),
    )
    if set(settings.groups) != set(GROUPS):
        raise ValueError(f"label_sample.groups must be exactly {GROUPS}")
    if settings.size != config["label_sample_size"]:
        raise ValueError(
            f"label_sample.groups add up to {settings.size}, "
            f"not label_sample_size ({config['label_sample_size']})"
        )
    if not 0 < settings.dev_size < settings.size:
        raise ValueError("label_sample.dev_size must be between 0 and the sample size")
    return settings


def labels_dir(config: dict[str, Any]) -> Path:
    """Return `paths.labels` (evals/labels)."""
    return Path(config["paths"]["labels"])


def write_new_csv(df: pd.DataFrame, path: Path, encoding: str = "utf-8") -> None:
    """Write `df` to `path`, refusing to replace a file that's already there."""
    if path.exists():
        raise FileExistsError(
            f"{path} already exists and is never overwritten. To redo it, delete "
            "it deliberately and record why in docs/decisions.md."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding=encoding)


def draw(con: duckdb.DuckDBPyConnection, settings: SampleSettings) -> pd.DataFrame:
    """Rank a seeded shuffle of each group's frame and keep the top of it.

    Returns SAMPLE_COLUMNS. `role` is `sample` for the first `groups[g]` of each
    group and `reserve` for up to `random_reserves` more in the random group.
    Raises ValueError if a frame is smaller than its group size.
    """
    today = date.today().isoformat()
    rows = []
    for group in GROUPS:
        frame = pd.Series(
            sorted(r[0] for r in con.execute(FRAME_SQL[group]).fetchall())
        )
        size = settings.groups[group]
        if len(frame) < size:
            raise ValueError(f"{group}: frame has {len(frame)} orgs, need {size}")
        wanted = size + (settings.random_reserves if group == "random" else 0)
        if len(frame) < wanted:
            logger.warning("%s: only %d reserves available", group, len(frame) - size)
        ranked = frame.sample(frac=1, random_state=settings.seed).head(wanted)
        for rank, ein in enumerate(ranked, start=1):
            role = "sample" if rank <= size else "reserve"
            rows.append((ein, group, rank, role, settings.seed, today))
        logger.info("%s: drew %d from a frame of %d", group, len(ranked), len(frame))
    return pd.DataFrame(rows, columns=SAMPLE_COLUMNS)


def dev_counts(groups: dict[str, int], dev_size: int) -> dict[str, int]:
    """Split `dev_size` across groups in proportion to their sizes.

    Largest remainder, ties broken in GROUPS order, so the counts always add
    up to `dev_size`. With 40/30/30 and a dev size of 40, that's 16/12/12.
    """
    total = sum(groups.values())
    exact = {group: groups[group] * dev_size / total for group in GROUPS}
    counts = {group: int(exact[group]) for group in GROUPS}
    leftover = dev_size - sum(counts.values())
    by_remainder = sorted(GROUPS, key=lambda g: -(exact[g] - counts[g]))
    for group in by_remainder[:leftover]:
        counts[group] += 1
    return counts


def select(
    con: duckdb.DuckDBPyConnection, sample: pd.DataFrame, settings: SampleSettings
) -> pd.DataFrame:
    """Pick the final organizations and assign each to dev or test.

    For each group, takes the first `groups[g]` sampled EINs, in rank order,
    that have a parsed filing. Reserves fill in for any that don't. The first
    `dev_counts` of each group go to dev, the rest to test. Raises ValueError
    if a group can't be filled. That usually means the build hasn't run since
    `draw`.
    """
    parsed = {r[0] for r in con.execute("SELECT ein FROM ingest.filings").fetchall()}
    dev_n = dev_counts(settings.groups, settings.dev_size)
    chosen = []
    for group in GROUPS:
        ranked = sample.loc[sample["sample_group"] == group].sort_values("rank")
        eligible = ranked.loc[ranked["ein"].isin(parsed)]
        size = settings.groups[group]
        if len(eligible) < size:
            raise ValueError(
                f"{group}: only {len(eligible)} of {len(ranked)} sampled orgs have a "
                f"parsed filing, need {size}. Run python -m src.ingest.build after "
                "draw, then check filing_index for why the rest weren't parsed."
            )
        picked = eligible.head(size).copy()
        used = int((picked["role"] == "reserve").sum())
        if used:
            logger.warning("%s: used %d reserves", group, used)
        picked["split"] = ["dev"] * dev_n[group] + ["test"] * (size - dev_n[group])
        chosen.append(picked)
    return pd.concat(chosen, ignore_index=True)


def _program_text(rows: pd.DataFrame) -> str:
    """One line per program: number, Part III line, description, expenses."""
    lines = []
    for row in rows.itertuples():
        line = f" [{row.part_iii_line}]" if pd.notna(row.part_iii_line) else ""
        expense = (
            f" (expenses ${int(row.expense_amt):,})"
            if pd.notna(row.expense_amt)
            else ""
        )
        description = (
            row.description if pd.notna(row.description) else "(no description)"
        )
        lines.append(f"{row.program_number}.{line} {description}{expense}")
    return "\n".join(lines)


def _schedule_o_text(rows: pd.DataFrame) -> str:
    """One line per mission or program note: [reference] explanation."""
    return "\n".join(
        f"[{row.reference or 'no reference'}] {row.explanation or ''}".strip()
        for row in rows.itertuples()
    )


def sheet_rows(con: duckdb.DuckDBPyConnection, eins: list[str]) -> pd.DataFrame:
    """Blank labeling rows for `eins`, from each one's latest parsed filing.

    Text comes from the mission, the 990 Part I activity line, the Part III
    programs, and the Schedule O notes flagged mission or program (D-013).
    Missing text is an empty string. `label` and `notes` are empty.
    """
    con.register("chosen", pd.DataFrame({"ein": eins}))
    filings = con.execute(
        """SELECT ein, name, tax_period, return_type, object_id,
                  mission, activity_or_mission
           FROM ingest.latest_filings WHERE ein IN (SELECT ein FROM chosen)"""
    ).df()
    programs = con.execute(
        """SELECT object_id, program_number, part_iii_line, description, expense_amt
           FROM ingest.programs
           WHERE object_id IN (SELECT object_id FROM ingest.latest_filings
                               WHERE ein IN (SELECT ein FROM chosen))
           ORDER BY object_id, program_number"""
    ).df()
    notes = con.execute(
        """SELECT object_id, reference, explanation FROM ingest.schedule_o
           WHERE is_mission_or_program
             AND object_id IN (SELECT object_id FROM ingest.latest_filings
                               WHERE ein IN (SELECT ein FROM chosen))
           ORDER BY object_id, entry_number"""
    ).df()
    con.unregister("chosen")

    by_filing = dict(tuple(programs.groupby("object_id")))
    notes_by_filing = dict(tuple(notes.groupby("object_id")))
    filings["programs"] = [
        _program_text(by_filing[oid]) if oid in by_filing else ""
        for oid in filings["object_id"]
    ]
    filings["schedule_o_text"] = [
        _schedule_o_text(notes_by_filing[oid]) if oid in notes_by_filing else ""
        for oid in filings["object_id"]
    ]
    filings["label"] = ""
    filings["notes"] = ""
    return filings[SHEET_COLUMNS].fillna("")


def write_sheets(
    con: duckdb.DuckDBPyConnection,
    chosen: pd.DataFrame,
    settings: SampleSettings,
    out_dir: Path,
) -> dict[str, Path]:
    """Write one blank, shuffled sheet per split. Refuses to replace either file."""
    paths = {split: out_dir / name for split, name in SHEET_FILES.items()}
    existing = [str(p) for p in paths.values() if p.exists()]
    if existing:
        raise FileExistsError(
            f"{', '.join(existing)} already exist and are never overwritten; "
            "hand labels may be in them."
        )
    for split, path in paths.items():
        eins = chosen.loc[chosen["split"] == split, "ein"].tolist()
        rows = sheet_rows(con, eins)
        # Shuffled, so the groups don't sit in blocks a labeler could notice.
        rows = rows.sample(frac=1, random_state=settings.seed).reset_index(drop=True)
        write_new_csv(rows, path, encoding=SHEET_ENCODING)
    return paths


def main() -> None:
    """CLI: `draw` the sample or write the labeling `sheet`s."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["draw", "sheet"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    config = load_config()
    settings = sample_settings(config)
    out_dir = labels_dir(config)
    sample_path = out_dir / LABEL_SAMPLE_FILE
    with duckdb.connect(str(database_path(config)), read_only=True) as con:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'ingest'"
            ).fetchall()
        }
        if "state_index" not in tables:
            raise SystemExit(
                "The database predates ingest.state_index. "
                "Run python -m src.ingest.build first."
            )
        if args.step == "draw":
            sample = draw(con, settings)
            write_new_csv(sample, sample_path)
            print(f"Wrote {sample_path}")
            print(sample.groupby(["sample_group", "role"]).size().to_string())
            print("Next: python -m src.ingest.build, then make_sample.py sheet")
        else:
            sample = pd.read_csv(sample_path, dtype={"ein": str, "seed": int})
            chosen = select(con, sample, settings)
            paths = write_sheets(con, chosen, settings, out_dir)
            print(chosen.groupby(["split", "sample_group"]).size().to_string())
            for path in paths.values():
                print(f"Wrote {path}")


if __name__ == "__main__":
    main()
