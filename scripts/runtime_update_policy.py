"""Shared native runtime update-package naming policy."""

from __future__ import annotations

LEGACY_UPDATE_PACKAGE = "conda-runtime"
WINDOWS_UPDATE_PACKAGE = "conda-runtime-v2"

UPDATE_PACKAGES = {
    "linux-64": LEGACY_UPDATE_PACKAGE,
    "linux-aarch64": LEGACY_UPDATE_PACKAGE,
    "osx-64": LEGACY_UPDATE_PACKAGE,
    "osx-arm64": LEGACY_UPDATE_PACKAGE,
    "win-64": WINDOWS_UPDATE_PACKAGE,
}


def update_package_name(subdir: str) -> str:
    try:
        return UPDATE_PACKAGES[subdir]
    except KeyError as error:
        raise ValueError(f"unsupported native platform: {subdir}") from error


def update_package_filename(subdir: str, version: str) -> str:
    return f"{update_package_name(subdir)}-{version}-0.conda"
