#!/usr/bin/env python3
"""Derive the Windows runtime root with its 0.9-compatible update source."""

from __future__ import annotations

import argparse
import copy
import re
import shutil
import tomllib
from pathlib import Path

from runtime_update_policy import LEGACY_UPDATE_PACKAGE, WINDOWS_UPDATE_PACKAGE

RUNTIME_FILES = ("conda.lock", "conda.toml", "runtime.condarc")
UPDATE_SECTION = "tool.conda-ship.update"


def manifest_update_package(manifest: dict[str, object]) -> str | None:
    try:
        tool = manifest["tool"]
        if not isinstance(tool, dict):
            return None
        conda_ship = tool["conda-ship"]
        if not isinstance(conda_ship, dict):
            return None
        update = conda_ship["update"]
        if not isinstance(update, dict):
            return None
        package = update["package"]
    except KeyError:
        return None
    return package if isinstance(package, str) else None


def derive_windows_manifest(source: str) -> str:
    source_manifest = tomllib.loads(source)
    if manifest_update_package(source_manifest) != LEGACY_UPDATE_PACKAGE:
        raise ValueError(
            "canonical runtime manifest must use the legacy conda-runtime package"
        )

    section = ""
    replacements = 0
    rendered = []
    for line in source.splitlines(keepends=True):
        section_match = re.match(r"^\s*\[([^]]+)]\s*(?:\r?\n)?$", line)
        if section_match:
            section = section_match.group(1)
        if section == UPDATE_SECTION:
            package_match = re.match(
                r'^(\s*package\s*=\s*)"([^"]*)"(\s*(?:#.*)?)(\r?\n)?$',
                line,
            )
            if package_match:
                if package_match.group(2) != LEGACY_UPDATE_PACKAGE:
                    raise ValueError(
                        "canonical runtime manifest has an unexpected update package"
                    )
                line = (
                    f'{package_match.group(1)}"{WINDOWS_UPDATE_PACKAGE}"'
                    f"{package_match.group(3)}{package_match.group(4) or ''}"
                )
                replacements += 1
        rendered.append(line)

    if replacements != 1:
        raise ValueError(
            "canonical runtime manifest must contain one update package assignment"
        )

    derived = "".join(rendered)
    derived_manifest = tomllib.loads(derived)
    if manifest_update_package(derived_manifest) != WINDOWS_UPDATE_PACKAGE:
        raise ValueError("derived Windows manifest has the wrong update package")
    normalized = copy.deepcopy(derived_manifest)
    normalized["tool"]["conda-ship"]["update"]["package"] = (  # type: ignore[index]
        LEGACY_UPDATE_PACKAGE
    )
    if normalized != source_manifest:
        raise ValueError(
            "derived Windows manifest changed more than the update package"
        )
    return derived


def prepare_windows_runtime_root(source: Path, destination: Path) -> Path:
    if not source.is_dir():
        raise ValueError(f"canonical runtime root is missing: {source}")
    if destination.exists():
        raise ValueError(f"derived Windows runtime root already exists: {destination}")

    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
    }
    expected = set(RUNTIME_FILES)
    if actual != expected:
        raise ValueError(
            f"canonical runtime root must contain {sorted(expected)!r}, "
            f"received {sorted(actual)!r}"
        )

    destination.mkdir(parents=True)
    shutil.copyfile(source / "conda.lock", destination / "conda.lock")
    shutil.copyfile(source / "runtime.condarc", destination / "runtime.condarc")
    (destination / "conda.toml").write_text(
        derive_windows_manifest((source / "conda.toml").read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    if (destination / "conda.lock").read_bytes() != (
        source / "conda.lock"
    ).read_bytes():
        raise ValueError("derived Windows root changed conda.lock")
    if (destination / "runtime.condarc").read_bytes() != (
        source / "runtime.condarc"
    ).read_bytes():
        raise ValueError("derived Windows root changed runtime.condarc")
    return destination.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        print(prepare_windows_runtime_root(args.source, args.destination))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
