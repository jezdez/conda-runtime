#!/usr/bin/env python3
"""Verify a conda-ship update package report against finalized files."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile

REPORT_KEYS = {
    "build_number",
    "filename",
    "package_name",
    "path",
    "payload_sha256",
    "payload_size",
    "platform",
    "runtime_version",
    "schema_version",
    "sha256",
    "size",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def packaged_payload(package: Path, platform: str) -> tuple[str, int]:
    expected_path = "conda.exe" if platform == "win-64" else "bin/conda"
    try:
        archive = ZipFile(package)
    except BadZipFile as error:
        raise SystemExit(
            f"update package is not a valid .conda archive: {package}"
        ) from error

    with archive:
        package_archives = [
            name
            for name in archive.namelist()
            if name.startswith("pkg-") and name.endswith(".tar.zst")
        ]
        if len(package_archives) != 1:
            raise SystemExit(
                "update package must contain exactly one pkg-*.tar.zst archive"
            )
        package_archive = package_archives[0]
        if Path(package_archive).name != package_archive:
            raise SystemExit("update package payload archive has an invalid path")

        tar = shutil.which("tar")
        if tar is None:
            raise SystemExit("tar is required to inspect the update package payload")

        with tempfile.TemporaryDirectory() as temporary_dir:
            archive_path = Path(temporary_dir) / package_archive
            with (
                archive.open(package_archive) as source,
                archive_path.open("wb") as target,
            ):
                shutil.copyfileobj(source, target)

            listing = subprocess.run(
                [tar, "-tf", archive_path],
                capture_output=True,
                check=False,
                encoding="utf-8",
            )
            if listing.returncode != 0:
                detail = listing.stderr.strip() or "unknown tar error"
                raise SystemExit(f"could not inspect update package payload: {detail}")
            files = [
                entry
                for entry in listing.stdout.splitlines()
                if entry and not entry.endswith("/")
            ]
            if len(files) != 1 or files[0].removeprefix("./") != expected_path:
                raise SystemExit(
                    f"update package payload must contain only {expected_path!r}, "
                    f"received {files!r}"
                )

            payload_path = Path(temporary_dir) / Path(expected_path).name
            with payload_path.open("wb") as payload:
                extracted = subprocess.run(
                    [tar, "-xOf", archive_path, files[0]],
                    stdout=payload,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            if extracted.returncode != 0:
                detail = extracted.stderr.decode(errors="replace").strip()
                raise SystemExit(
                    f"could not extract update package payload: {detail or 'unknown tar error'}"
                )
            return sha256(payload_path), payload_path.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("binary", type=Path)
    parser.add_argument("package", type=Path)
    parser.add_argument("version")
    parser.add_argument("platform")
    args = parser.parse_args()

    report = json.loads(args.report.read_text())
    if set(report) != REPORT_KEYS:
        raise SystemExit(f"unexpected package report fields: {sorted(report)!r}")
    if Path(report["path"]).resolve() != args.package.resolve():
        raise SystemExit("update package report points to a different package")

    binary_sha256 = sha256(args.binary)
    binary_size = args.binary.stat().st_size
    payload_sha256, payload_size = packaged_payload(args.package, args.platform)
    if (payload_sha256, payload_size) != (binary_sha256, binary_size):
        raise SystemExit(
            "update package payload does not match the finalized executable"
        )

    expected_filename = f"conda-runtime-{args.version}-0.conda"
    expected = {
        "schema_version": 1,
        "filename": expected_filename,
        "package_name": "conda-runtime",
        "runtime_version": args.version,
        "build_number": 0,
        "platform": args.platform,
        "path": report["path"],
        "sha256": sha256(args.package),
        "size": args.package.stat().st_size,
        "payload_sha256": binary_sha256,
        "payload_size": binary_size,
    }
    if report != expected:
        raise SystemExit(
            "update package report does not match finalized files:\n"
            f"expected {expected!r}\nreceived {report!r}"
        )


if __name__ == "__main__":
    main()
