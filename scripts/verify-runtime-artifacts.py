#!/usr/bin/env python3
"""Validate collected runtime release and update package files."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

TARGETS = {
    "linux-64": "x86_64-unknown-linux-gnu",
    "linux-aarch64": "aarch64-unknown-linux-gnu",
    "osx-64": "x86_64-apple-darwin",
    "osx-arm64": "aarch64-apple-darwin",
    "win-64": "x86_64-pc-windows-msvc.exe",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("version")
    parser.add_argument("--write-checksums", action="store_true")
    args = parser.parse_args()

    release_dir = args.root / "release-assets"
    package_dir = args.root / "update-packages"
    expected_assets = {f"conda-{target}" for target in TARGETS.values()}
    expected_packages = {
        Path(subdir) / f"conda-runtime-{args.version}-0.conda" for subdir in TARGETS
    }

    actual_assets = {
        path.name for path in release_dir.iterdir() if path.is_file() and path.name != "SHA256SUMS"
    }
    actual_packages = {
        path.relative_to(package_dir) for path in package_dir.rglob("*") if path.is_file()
    }

    if actual_assets != expected_assets:
        raise SystemExit(
            f"unexpected release assets: expected {sorted(expected_assets)!r}, "
            f"received {sorted(actual_assets)!r}"
        )
    if actual_packages != expected_packages:
        raise SystemExit(
            "unexpected update packages: expected "
            f"{sorted(map(str, expected_packages))!r}, received "
            f"{sorted(map(str, actual_packages))!r}"
        )

    if args.write_checksums:
        checksum_path = release_dir / "SHA256SUMS"
        lines = [f"{sha256(release_dir / name)}  {name}\n" for name in sorted(expected_assets)]
        checksum_path.write_text("".join(lines))


if __name__ == "__main__":
    main()
