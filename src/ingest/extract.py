"""Extract only the manifest's 990 XML files from the IRS zip partitions.

Each zip's file list is read without extracting anything, and each manifest
OBJECT_ID is mapped to the zip that actually holds it. The index's XML_BATCH_ID
is not trusted for this, because it is wrong for some filings (D-011). Only the
matching members are written to `paths.interim/xml/<OBJECT_ID>_public.xml`, and
files already there are skipped, so reruns are fast.

Most zips use standard Deflate, which `zipfile` reads. A few 2025 zips use
Deflate64, which `zipfile` can list but not decompress, so those members are
extracted with the 7-Zip command-line tool (D-011).

Run with `python -m src.ingest.extract` after `python -m src.ingest.manifest`.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from src.config import load_config
from src.ingest.manifest import manifest_path

logger = logging.getLogger(__name__)

MEMBER_PATTERN = re.compile(r"(\d{18})_public\.xml")
# Compression methods zipfile can decompress. Anything else (Deflate64 is
# method 9) goes to 7-Zip.
ZIPFILE_METHODS = frozenset(
    {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
)
SEVEN_ZIP_LOCATIONS = [
    Path(r"C:\Program Files\7-Zip\7z.exe"),
    Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
]
MISSING_REPORT_COLUMNS = ["EIN", "TAX_PERIOD", "OBJECT_ID", "XML_BATCH_ID"]


@dataclass(frozen=True)
class Located:
    """One manifest filing and the zip member that holds it."""

    object_id: str
    zip_path: Path
    info: zipfile.ZipInfo


@dataclass
class ExtractResult:
    """OBJECT_IDs grouped by what happened to them in one extract run."""

    extracted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def zip_paths(config: dict[str, Any]) -> list[Path]:
    """Return every zip in `paths.raw`, sorted by name."""
    return sorted(Path(config["paths"]["raw"]).glob("*.zip"))


def xml_dir(config: dict[str, Any]) -> Path:
    """Return the extracted-XML directory, `paths.interim/xml`."""
    return Path(config["paths"]["interim"]) / "xml"


def missing_report_path(config: dict[str, Any]) -> Path:
    """Return the path of the not-found report, `paths.interim/extract_missing.csv`."""
    return Path(config["paths"]["interim"]) / "extract_missing.csv"


def xml_path(out_dir: Path, object_id: str) -> Path:
    """Return where the XML for `object_id` is written inside `out_dir`."""
    return out_dir / f"{object_id}_public.xml"


def object_id_from_member(name: str) -> str | None:
    """Return the OBJECT_ID in a member name like `dir/<id>_public.xml`, else None.

    Some zips put members under a folder named after the batch and some don't,
    so only the last path component is matched.
    """
    match = MEMBER_PATTERN.fullmatch(name.rsplit("/", 1)[-1])
    return match.group(1) if match else None


def list_zip(path: Path) -> dict[str, zipfile.ZipInfo]:
    """Read a zip's file list (nothing is extracted) and map OBJECT_ID to member."""
    with zipfile.ZipFile(path) as zf:
        members = {}
        for info in zf.infolist():
            object_id = object_id_from_member(info.filename)
            if object_id is not None and not info.is_dir():
                members[object_id] = info
    logger.info("Listed %d XML members in %s", len(members), path.name)
    return members


def locate_filings(
    object_ids: Iterable[str], paths: Iterable[Path]
) -> tuple[dict[str, Located], list[str]]:
    """Find the zip member for each OBJECT_ID by scanning each zip's file list.

    Returns (located, missing): located maps OBJECT_ID to its member, and missing
    is the sorted list of IDs found in no zip. If an ID is in more than one zip,
    the first zip in `paths` order wins and a warning is logged.
    """
    wanted = set(object_ids)
    located: dict[str, Located] = {}
    for path in paths:
        for object_id, info in list_zip(path).items():
            if object_id not in wanted:
                continue
            if object_id in located:
                logger.warning(
                    "%s is in both %s and %s; keeping the first",
                    object_id,
                    located[object_id].zip_path.name,
                    path.name,
                )
                continue
            located[object_id] = Located(object_id, path, info)
    missing = sorted(wanted - located.keys())
    logger.info("Located %d of %d filings", len(located), len(wanted))
    return located, missing


def is_extracted(path: Path, info: zipfile.ZipInfo) -> bool:
    """True if `path` exists and has the member's uncompressed size."""
    return path.is_file() and path.stat().st_size == info.file_size


def needs_7zip(info: zipfile.ZipInfo) -> bool:
    """True if `zipfile` can't decompress this member (e.g. Deflate64)."""
    return info.compress_type not in ZIPFILE_METHODS


def find_7zip() -> Path | None:
    """Return the 7-Zip executable from PATH or a default install folder, or None."""
    on_path = shutil.which("7z")
    if on_path:
        return Path(on_path)
    return next((p for p in SEVEN_ZIP_LOCATIONS if p.is_file()), None)


def _extract_with_zipfile(
    zip_path: Path, items: list[Located], out_dir: Path, result: ExtractResult
) -> None:
    """Extract `items` from one zip with zipfile, writing each file atomically."""
    with zipfile.ZipFile(zip_path) as zf:
        for item in items:
            target = xml_path(out_dir, item.object_id)
            partial = target.with_name(target.name + ".part")
            with zf.open(item.info) as src, partial.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            os.replace(partial, target)
            result.extracted.append(item.object_id)


def _extract_with_7zip(
    sevenzip: Path,
    zip_path: Path,
    items: list[Located],
    out_dir: Path,
    result: ExtractResult,
) -> None:
    """Extract `items` from one zip with a single 7-Zip call.

    Files go to a staging folder inside `out_dir` and are moved into place only
    if their size matches the zip's record. Any that fail are marked unreadable.
    """
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".7z_") as tmp:
        stage = Path(tmp) / "files"
        listfile = Path(tmp) / "members.txt"
        listfile.write_text(
            "\n".join(item.info.filename for item in items) + "\n", encoding="utf-8"
        )
        command = [
            str(sevenzip),
            "e",  # extract without folder paths
            str(zip_path),
            f"-o{stage}",
            "-y",
            "-scsUTF-8",
            "-bso0",
            "-bsp0",
            f"@{listfile}",
        ]
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        if run.returncode != 0:
            logger.error(
                "7-Zip exited %d on %s: %s",
                run.returncode,
                zip_path.name,
                run.stderr.strip(),
            )
        for item in items:
            staged = stage / item.info.filename.rsplit("/", 1)[-1]
            if is_extracted(staged, item.info):
                os.replace(staged, xml_path(out_dir, item.object_id))
                result.extracted.append(item.object_id)
            else:
                result.unreadable.append(item.object_id)


def extract_filings(
    located: Iterable[Located], out_dir: Path, sevenzip: Path | None
) -> ExtractResult:
    """Extract each located filing to `out_dir`, skipping ones already there.

    Members zipfile can read are extracted directly. The rest go to `sevenzip`.
    If `sevenzip` is None, they are reported as unreadable instead.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    result = ExtractResult()
    by_zip: dict[Path, list[Located]] = defaultdict(list)
    for item in located:
        if is_extracted(xml_path(out_dir, item.object_id), item.info):
            result.skipped.append(item.object_id)
        else:
            by_zip[item.zip_path].append(item)

    for zip_path, items in sorted(by_zip.items()):
        plain = [item for item in items if not needs_7zip(item.info)]
        special = [item for item in items if needs_7zip(item.info)]
        if plain:
            _extract_with_zipfile(zip_path, plain, out_dir, result)
        if special and sevenzip is None:
            logger.error(
                "7-Zip not found; can't extract %d Deflate64 members from %s",
                len(special),
                zip_path.name,
            )
            result.unreadable.extend(item.object_id for item in special)
        elif special:
            _extract_with_7zip(sevenzip, zip_path, special, out_dir, result)
        logger.info("Processed %d filings from %s", len(items), zip_path.name)
    return result


def main() -> None:
    """Extract the manifest's XML files and print found, skipped, and missing counts."""
    logging.basicConfig(level=logging.WARNING)
    config = load_config()
    manifest = duckdb.read_parquet(manifest_path(config).as_posix()).df()
    paths = zip_paths(config)
    located, missing = locate_filings(manifest["OBJECT_ID"], paths)

    batch = dict(zip(manifest["OBJECT_ID"], manifest["XML_BATCH_ID"], strict=True))
    moved = sum(item.zip_path.stem != batch[oid] for oid, item in located.items())
    sevenzip = find_7zip()
    result = extract_filings(located.values(), xml_dir(config), sevenzip)

    rows = [
        ("manifest filings", len(manifest)),
        ("zips scanned", len(paths)),
        ("located in a zip", len(located)),
        ("  in a different zip than XML_BATCH_ID", moved),
        ("not found in any zip", len(missing)),
        ("extracted this run", len(result.extracted)),
        ("already extracted (skipped)", len(result.skipped)),
        ("unreadable", len(result.unreadable)),
    ]
    width = max(len(name) for name, _ in rows)
    for name, count in rows:
        print(f"{name:<{width}}  {count:>7,}")
    print(f"7-Zip: {sevenzip or 'not found'}")

    report = missing_report_path(config)
    missing_rows = manifest.loc[
        manifest["OBJECT_ID"].isin(missing), MISSING_REPORT_COLUMNS
    ]
    missing_rows.to_csv(report, index=False)
    if missing:
        print(f"\nNot found in any zip (first 20 of {len(missing)}; all in {report}):")
        print(missing_rows.head(20).to_string(index=False))
    if result.unreadable:
        print(f"\nUnreadable (first 20 of {len(result.unreadable)}):")
        print("\n".join(sorted(result.unreadable)[:20]))
    print(f"\nXML in {xml_dir(config)}")


if __name__ == "__main__":
    main()
