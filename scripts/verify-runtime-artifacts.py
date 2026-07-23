#!/usr/bin/env python3
"""Validate collected runtime release and update package files."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

TARGETS = {
    "linux-64": "x86_64-unknown-linux-gnu",
    "linux-aarch64": "aarch64-unknown-linux-gnu",
    "osx-64": "x86_64-apple-darwin",
    "osx-arm64": "aarch64-apple-darwin",
    "win-64": "x86_64-pc-windows-msvc.exe",
}
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
INSTALLER_TEMPLATES = {
    "install.sh": REPOSITORY_ROOT / "installers/install.sh.in",
    "install.ps1": REPOSITORY_ROOT / "installers/install.ps1.in",
}
VERSION_PATTERN = re.compile(r"[0-9]+[.][0-9]+[.][0-9]+(?:[.]post[0-9]+)?")
VERSION_TOKEN = "@CONDA_RUNTIME_VERSION@"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_installers(version: str) -> dict[str, bytes]:
    rendered: dict[str, bytes] = {}
    for asset_name, template_path in INSTALLER_TEMPLATES.items():
        template = template_path.read_text(encoding="utf-8")
        if template.count(VERSION_TOKEN) != 1:
            raise SystemExit(
                f"{template_path} must contain exactly one {VERSION_TOKEN} token"
            )
        rendered[asset_name] = template.replace(VERSION_TOKEN, version).encode("utf-8")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("version")
    parser.add_argument("--write-checksums", action="store_true")
    args = parser.parse_args()

    if VERSION_PATTERN.fullmatch(args.version) is None:
        raise SystemExit(
            "runtime versions must use X.Y.Z or X.Y.Z.postN, such as 26.5.3"
        )

    release_dir = args.root / "release-assets"
    package_dir = args.root / "update-packages"
    installers = render_installers(args.version)
    if args.write_checksums:
        for asset_name, contents in installers.items():
            (release_dir / asset_name).write_bytes(contents)

    expected_assets = {
        *(f"conda-{target}" for target in TARGETS.values()),
        *installers,
    }
    expected_packages = {
        Path(subdir) / f"conda-runtime-{args.version}-0.conda" for subdir in TARGETS
    }

    actual_assets = {
        path.name
        for path in release_dir.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    }
    actual_packages = {
        path.relative_to(package_dir)
        for path in package_dir.rglob("*")
        if path.is_file()
    }

    if actual_assets != expected_assets:
        raise SystemExit(
            f"unexpected release assets: expected {sorted(expected_assets)!r}, "
            f"received {sorted(actual_assets)!r}"
        )
    for asset_name, contents in installers.items():
        if (release_dir / asset_name).read_bytes() != contents:
            raise SystemExit(
                f"{asset_name} does not match runtime version {args.version}"
            )
    if actual_packages != expected_packages:
        raise SystemExit(
            "unexpected update packages: expected "
            f"{sorted(map(str, expected_packages))!r}, received "
            f"{sorted(map(str, actual_packages))!r}"
        )

    if args.write_checksums:
        checksum_path = release_dir / "SHA256SUMS"
        lines = [
            f"{sha256(release_dir / name)}  {name}\n"
            for name in sorted(expected_assets)
        ]
        checksum_path.write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
