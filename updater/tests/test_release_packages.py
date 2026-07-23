from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts/publish-runtime-packages.py"
SPEC = importlib.util.spec_from_file_location("publish_runtime_packages", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
release_packages = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_packages
SPEC.loader.exec_module(release_packages)


def runtime_package(
    tmp_path: Path,
    subdir: str = "linux-64",
    version: str = "26.5.3.post1",
):
    path = tmp_path / subdir / f"conda-runtime-{version}-0.conda"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"runtime")
    return release_packages.RuntimePackage(
        path=path,
        subdir=subdir,
        version=version,
        sha256=release_packages.file_sha256(path),
        size=path.stat().st_size,
    )


def api_metadata(package):
    return {
        "files": [
            {
                "basename": package.basename,
                "sha256": package.sha256,
                "size": package.size,
                "version": package.version,
                "labels": ["main"],
                "attrs": {
                    "name": "conda-runtime",
                    "version": package.version,
                    "build": "0",
                    "build_number": 0,
                    "subdir": package.subdir,
                    **release_packages.NATIVE_IDENTITIES[package.subdir],
                },
            }
        ]
    }


def test_remote_metadata_must_match_local_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    package = runtime_package(tmp_path)

    monkeypatch.setattr(
        release_packages,
        "get_json",
        lambda url, **_kwargs: (
            api_metadata(package)
            if "api.anaconda.org" in url
            else {
                "packages.conda": {
                    package.filename: {
                        "name": "conda-runtime",
                        "version": package.version,
                        "build": "0",
                        "build_number": 0,
                        "subdir": package.subdir,
                        "sha256": package.sha256,
                        "size": package.size,
                    }
                }
            }
        ),
    )
    assert release_packages.api_has(package, "jezdez")
    assert release_packages.repodata_has(package, "jezdez")

    mismatched = api_metadata(package)
    mismatched["files"][0]["sha256"] = "0" * 64
    monkeypatch.setattr(
        release_packages,
        "get_json",
        lambda _url, **_kwargs: mismatched,
    )
    with pytest.raises(release_packages.RemoteMismatch, match="sha256"):
        release_packages.api_has(package, "jezdez")

    mismatched = api_metadata(package)
    mismatched["files"][0]["attrs"]["target-triplet"] = "wrong"
    monkeypatch.setattr(
        release_packages,
        "get_json",
        lambda _url, **_kwargs: mismatched,
    )
    with pytest.raises(release_packages.RemoteMismatch, match="target-triplet"):
        release_packages.api_has(package, "jezdez")


@pytest.mark.parametrize(
    ("subdir", "expected"),
    [
        (
            "linux-64",
            {
                "platform": "linux",
                "arch": "x86_64",
                "machine": "x86_64",
                "operatingsystem": "linux",
                "target-triplet": "x86_64-any-linux",
            },
        ),
        (
            "linux-aarch64",
            {
                "platform": "linux",
                "arch": "aarch64",
                "machine": "aarch64",
                "operatingsystem": "linux",
                "target-triplet": "aarch64-any-linux",
            },
        ),
        (
            "osx-64",
            {
                "platform": "osx",
                "arch": "x86_64",
                "machine": "x86_64",
                "operatingsystem": "darwin",
                "target-triplet": "x86_64-any-darwin",
            },
        ),
        (
            "osx-arm64",
            {
                "platform": "osx",
                "arch": "arm64",
                "machine": "arm64",
                "operatingsystem": "darwin",
                "target-triplet": "arm64-any-darwin",
            },
        ),
        (
            "win-64",
            {
                "platform": "win",
                "arch": "x86_64",
                "machine": "x86_64",
                "operatingsystem": "win32",
                "target-triplet": "x86_64-any-win32",
            },
        ),
    ],
)
def test_native_identity_matches_anaconda_client(
    subdir: str,
    expected: dict[str, str],
):
    assert release_packages.NATIVE_IDENTITIES[subdir] == expected


def test_publish_skips_exact_files_and_uploads_only_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    existing = runtime_package(tmp_path / "existing")
    missing = runtime_package(tmp_path / "missing", "osx-arm64")
    uploaded = set()
    commands = []

    def fake_has(package, *_args):
        return package is existing or package.basename in uploaded

    def fake_run(command, check):
        assert check is True
        commands.append(command)
        uploaded.add(missing.basename)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release_packages, "api_has", fake_has)
    monkeypatch.setattr(release_packages, "repodata_has", fake_has)
    monkeypatch.setattr(release_packages.subprocess, "run", fake_run)

    release_packages.publish(
        [existing, missing],
        owner="jezdez",
        timeout=1,
        interval=0,
    )

    assert len(commands) == 1
    assert commands == [
        [
            "anaconda",
            "upload",
            "--user",
            "jezdez",
            "--label",
            "main",
            "--summary",
            "Standalone conda runtime",
            "--keep-basename",
            "--no-progress",
            str(missing.path),
        ]
    ]
