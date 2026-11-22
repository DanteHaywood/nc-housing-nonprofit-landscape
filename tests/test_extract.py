import logging
import subprocess
import zipfile
from pathlib import Path

import pytest

from src.ingest.extract import (
    Located,
    extract_filings,
    find_7zip,
    list_zip,
    locate_filings,
    needs_7zip,
    object_id_from_member,
    xml_dir,
    xml_path,
    zip_paths,
)

# Test zips are built in tmp_path from these few rows, mirroring both IRS layouts:
# 2024 zips put members under a batch folder, 2025 zips are flat.
WANTED_A = "202410229349200001"
WANTED_B = "202510139349300060"
UNWANTED = "202410229349200050"
DUPLICATE = "202420219349200040"


def xml_for(object_id: str) -> bytes:
    return f'<?xml version="1.0"?><Return id="{object_id}"/>'.encode()


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


# Two zips: one with a batch folder (like 2024), one flat (like 2025). DUPLICATE
# appears in both so the first-zip-wins rule can be tested.
@pytest.fixture
def raw(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    make_zip(
        raw / "2024_TEOS_XML_01A.zip",
        {
            f"2024_TEOS_XML_01A/{WANTED_A}_public.xml": xml_for(WANTED_A),
            f"2024_TEOS_XML_01A/{UNWANTED}_public.xml": xml_for(UNWANTED),
            f"2024_TEOS_XML_01A/{DUPLICATE}_public.xml": xml_for(DUPLICATE),
            "2024_TEOS_XML_01A/README.txt": b"not a filing",
        },
    )
    make_zip(
        raw / "2025_TEOS_XML_01A.zip",
        {
            f"{WANTED_B}_public.xml": xml_for(WANTED_B),
            f"{DUPLICATE}_public.xml": b"<second copy/>",
        },
    )
    return raw


def locate(raw: Path, ids: list[str]) -> dict[str, Located]:
    located, _ = locate_filings(ids, sorted(raw.glob("*.zip")))
    return located


# Paths come from config: every zip in the raw dir (sorted), XML under interim.
def test_paths_come_from_config(raw: Path) -> None:
    config = {"paths": {"raw": str(raw), "interim": "data/interim"}}
    assert [p.name for p in zip_paths(config)] == [
        "2024_TEOS_XML_01A.zip",
        "2025_TEOS_XML_01A.zip",
    ]
    assert xml_dir(config) == Path("data/interim/xml")


# Member names match with or without a batch folder; anything else is ignored.
def test_object_id_from_member() -> None:
    assert object_id_from_member(f"2024_TEOS_XML_01A/{WANTED_A}_public.xml") == (
        WANTED_A
    )
    assert object_id_from_member(f"{WANTED_B}_public.xml") == WANTED_B
    assert object_id_from_member("2024_TEOS_XML_01A/README.txt") is None
    assert object_id_from_member("2024_TEOS_XML_01A/") is None
    assert object_id_from_member("12345_public.xml") is None


# Listing a zip reads only its file list: nothing is written to disk.
def test_list_zip_reads_names_without_extracting(raw: Path) -> None:
    before = set(raw.rglob("*"))
    members = list_zip(raw / "2024_TEOS_XML_01A.zip")
    assert set(members) == {WANTED_A, UNWANTED, DUPLICATE}
    assert set(raw.rglob("*")) == before


# Each wanted ID maps to the zip that holds it, and IDs in no zip are reported.
def test_locate_filings_maps_ids_and_reports_missing(raw: Path) -> None:
    ghost = "202599999999999999"
    located, missing = locate_filings(
        [WANTED_A, WANTED_B, ghost], sorted(raw.glob("*.zip"))
    )
    assert {oid: item.zip_path.name for oid, item in located.items()} == {
        WANTED_A: "2024_TEOS_XML_01A.zip",
        WANTED_B: "2025_TEOS_XML_01A.zip",
    }
    assert missing == [ghost]


# An ID found in two zips keeps the first zip and logs a warning.
def test_locate_filings_duplicate_keeps_first(
    raw: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        located = locate(raw, [DUPLICATE])
    assert located[DUPLICATE].zip_path.name == "2024_TEOS_XML_01A.zip"
    assert "in both" in caplog.text


# Only the wanted members are written, named by OBJECT_ID, with their contents.
def test_extract_filings_writes_only_wanted(raw: Path, tmp_path: Path) -> None:
    out = tmp_path / "xml"
    result = extract_filings(locate(raw, [WANTED_A, WANTED_B]).values(), out, None)
    assert sorted(result.extracted) == [WANTED_A, WANTED_B]
    assert sorted(p.name for p in out.iterdir()) == [
        f"{WANTED_A}_public.xml",
        f"{WANTED_B}_public.xml",
    ]
    assert xml_path(out, WANTED_B).read_bytes() == xml_for(WANTED_B)


# A rerun skips files already extracted and doesn't rewrite them.
def test_rerun_skips_existing_files(raw: Path, tmp_path: Path) -> None:
    out = tmp_path / "xml"
    located = locate(raw, [WANTED_A, WANTED_B])
    extract_filings(located.values(), out, None)
    mtime = xml_path(out, WANTED_A).stat().st_mtime_ns
    result = extract_filings(located.values(), out, None)
    assert result.extracted == []
    assert sorted(result.skipped) == [WANTED_A, WANTED_B]
    assert xml_path(out, WANTED_A).stat().st_mtime_ns == mtime


# A file with the wrong size (e.g. from an interrupted run) is extracted again.
def test_truncated_file_is_reextracted(raw: Path, tmp_path: Path) -> None:
    out = tmp_path / "xml"
    out.mkdir()
    xml_path(out, WANTED_A).write_bytes(b"<Ret")
    result = extract_filings(locate(raw, [WANTED_A]).values(), out, None)
    assert result.extracted == [WANTED_A]
    assert xml_path(out, WANTED_A).read_bytes() == xml_for(WANTED_A)


# Without 7-Zip, a Deflate64 member is reported as unreadable, not a crash.
# The ZipInfo is built by hand because Python can't write Deflate64.
def test_deflate64_without_7zip_is_unreadable(tmp_path: Path) -> None:
    info = zipfile.ZipInfo(f"{WANTED_B}_public.xml")
    info.compress_type = 9
    info.file_size = 100
    assert needs_7zip(info)
    item = Located(WANTED_B, tmp_path / "never_opened.zip", info)
    result = extract_filings([item], tmp_path / "xml", None)
    assert result.unreadable == [WANTED_B]
    assert result.extracted == []


SEVEN_ZIP = find_7zip()


# With 7-Zip installed, a real Deflate64 zip is listed by zipfile, flagged as
# needing 7-Zip, and extracted through it. Skipped where 7-Zip isn't installed.
@pytest.mark.skipif(SEVEN_ZIP is None, reason="7-Zip not installed")
def test_deflate64_extracts_with_7zip(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    # 7-Zip stores tiny files uncompressed, so give it something worth compressing.
    body = xml_for(WANTED_A) + b"<!-- padding -->" * 2000
    (src / f"{WANTED_A}_public.xml").write_bytes(body)
    (src / f"{UNWANTED}_public.xml").write_bytes(body)
    archive = tmp_path / "raw" / "2025_TEOS_XML_05A.zip"
    archive.parent.mkdir()
    subprocess.run(
        [str(SEVEN_ZIP), "a", "-tzip", "-mm=Deflate64", "-bso0", str(archive), "*"],
        cwd=src,
        check=True,
        capture_output=True,
    )
    located = locate(archive.parent, [WANTED_A])
    assert needs_7zip(located[WANTED_A].info)

    out = tmp_path / "xml"
    result = extract_filings(located.values(), out, SEVEN_ZIP)
    assert result.extracted == [WANTED_A]
    assert result.unreadable == []
    assert xml_path(out, WANTED_A).read_bytes() == body
    # The staging folder is cleaned up and the unwanted member was never written.
    assert [p.name for p in out.iterdir()] == [f"{WANTED_A}_public.xml"]
