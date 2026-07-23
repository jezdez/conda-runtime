#!/usr/bin/env python3
"""Verify a conda-ship update package report against finalized files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

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
        "payload_sha256": sha256(args.binary),
        "payload_size": args.binary.stat().st_size,
    }
    if report != expected:
        raise SystemExit(
            "update package report does not match finalized files:\n"
            f"expected {expected!r}\nreceived {report!r}"
        )


if __name__ == "__main__":
    main()
