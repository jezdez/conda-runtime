#!/usr/bin/env python3
"""Publish native runtime update packages to Anaconda.org."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API_URL = "https://api.anaconda.org"
CHANNEL_URL = "https://conda.anaconda.org"
CHANNEL = "main"
PACKAGE_NAME = "conda-runtime"
SUBDIRS = ("linux-64", "linux-aarch64", "osx-64", "osx-arm64", "win-64")


class RemoteMismatch(RuntimeError):
    """A published package does not match the local package."""


@dataclass(frozen=True)
class RuntimePackage:
    path: Path
    subdir: str
    version: str
    sha256: str
    size: int

    @property
    def filename(self) -> str:
        return f"{PACKAGE_NAME}-{self.version}-0.conda"

    @property
    def basename(self) -> str:
        return f"{self.subdir}/{self.filename}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_packages(root: Path, version: str) -> list[RuntimePackage]:
    expected = {
        Path(subdir) / f"{PACKAGE_NAME}-{version}-0.conda" for subdir in SUBDIRS
    }
    actual = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
    if actual != expected:
        raise SystemExit(
            f"expected {sorted(map(str, expected))!r}, received "
            f"{sorted(map(str, actual))!r}"
        )
    return [
        RuntimePackage(
            path=root / relative,
            subdir=relative.parent.name,
            version=version,
            sha256=file_sha256(root / relative),
            size=(root / relative).stat().st_size,
        )
        for relative in sorted(expected)
    ]


def get_json(url: str, *, missing_ok: bool = False) -> dict[str, object] | None:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "conda-runtime-release",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.load(response)
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise
    if not isinstance(value, dict):
        raise RuntimeError(f"{url} returned invalid JSON")
    return value


def verify_fields(
    source: str,
    actual: dict[str, object],
    expected: dict[str, object],
) -> None:
    differences = {
        key: (actual.get(key), value)
        for key, value in expected.items()
        if actual.get(key) != value
    }
    if differences:
        raise RemoteMismatch(f"{source} differs from local: {differences!r}")


def api_has(package: RuntimePackage, owner: str) -> bool:
    metadata = get_json(
        f"{API_URL}/package/{owner}/{PACKAGE_NAME}",
        missing_ok=True,
    )
    if metadata is None:
        return False
    files = metadata.get("files")
    if not isinstance(files, list):
        raise RuntimeError("Anaconda.org package metadata has no files list")
    matches = [
        item
        for item in files
        if isinstance(item, dict) and item.get("basename") == package.basename
    ]
    if not matches:
        return False
    if len(matches) != 1:
        raise RemoteMismatch(f"multiple remote files match {package.basename}")

    distribution = matches[0]
    verify_fields(
        f"Anaconda.org file {package.basename}",
        distribution,
        {
            "basename": package.basename,
            "sha256": package.sha256,
            "size": package.size,
            "version": package.version,
        },
    )
    labels = distribution.get("labels")
    if not isinstance(labels, list) or CHANNEL not in labels:
        raise RemoteMismatch(f"{package.basename} is not labeled {CHANNEL!r}")
    attrs = distribution.get("attrs")
    if not isinstance(attrs, dict):
        raise RemoteMismatch(f"{package.basename} has invalid attributes")
    verify_fields(
        f"Anaconda.org attributes for {package.basename}",
        attrs,
        {
            "name": PACKAGE_NAME,
            "version": package.version,
            "build": "0",
            "build_number": 0,
            "subdir": package.subdir,
        },
    )
    return True


def repodata_has(package: RuntimePackage, owner: str) -> bool:
    metadata = get_json(
        f"{CHANNEL_URL}/{owner}/{package.subdir}/repodata.json"
        f"?conda-runtime-release={time.time_ns()}",
        missing_ok=True,
    )
    if metadata is None:
        return False
    records = metadata.get("packages.conda")
    if not isinstance(records, dict):
        raise RuntimeError(f"{package.subdir} repodata has no packages.conda mapping")
    record = records.get(package.filename)
    if record is None:
        return False
    if not isinstance(record, dict):
        raise RemoteMismatch(f"{package.basename} has invalid repodata")
    verify_fields(
        f"repodata for {package.basename}",
        record,
        {
            "name": PACKAGE_NAME,
            "version": package.version,
            "build": "0",
            "build_number": 0,
            "subdir": package.subdir,
            "sha256": package.sha256,
            "size": package.size,
        },
    )
    return True


def publish(
    packages: list[RuntimePackage],
    *,
    owner: str,
    timeout: float,
    interval: float,
) -> None:
    for package in packages:
        if api_has(package, owner) or repodata_has(package, owner):
            print(f"Anaconda.org already contains {package.basename}")
            continue
        subprocess.run(
            [
                "rattler-build",
                "upload",
                "anaconda",
                "--owner",
                owner,
                "--channel",
                CHANNEL,
                str(package.path),
            ],
            check=True,
        )

    deadline = time.monotonic() + timeout
    while True:
        pending = []
        for package in packages:
            try:
                if not api_has(package, owner) or not repodata_has(package, owner):
                    pending.append(package.basename)
            except (OSError, urllib.error.URLError):
                pending.append(package.basename)
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise SystemExit(f"timed out waiting for Anaconda.org: {pending!r}")
        print(f"Waiting for Anaconda.org metadata: {pending!r}")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("version")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()

    publish(
        discover_packages(args.root, args.version),
        owner=args.owner,
        timeout=args.timeout,
        interval=args.interval,
    )
    print(f"Published and verified {PACKAGE_NAME} {args.version} for all platforms.")


if __name__ == "__main__":
    main()
