from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts/publish-runtime-packages.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("publish_runtime_packages", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
release_packages = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_packages
SPEC.loader.exec_module(release_packages)


def runtime_package(
    tmp_path: Path,
    subdir: str = "linux-64",
    version: str = "26.7.1.post2",
):
    path = tmp_path / subdir / release_packages.update_package_filename(subdir, version)
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
    missing = runtime_package(tmp_path / "missing", "win-64")
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


@pytest.mark.parametrize("version", ["26.7.1.post2", "26.7.1.post3", "99.0.0"])
def test_windows_versions_use_the_v2_update_source(tmp_path: Path, version: str):
    package = runtime_package(tmp_path, "win-64", version)

    assert package.package_name == "conda-runtime-v2"
    assert package.basename == f"win-64/conda-runtime-v2-{version}-0.conda"


@pytest.mark.parametrize("subdir", ["linux-64", "linux-aarch64", "osx-64", "osx-arm64"])
def test_non_windows_versions_keep_the_legacy_update_source(tmp_path: Path, subdir: str):
    package = runtime_package(tmp_path, subdir, "99.0.0")

    assert package.package_name == "conda-runtime"
    assert package.filename == "conda-runtime-99.0.0-0.conda"


def test_discovery_rejects_legacy_windows_package(tmp_path: Path):
    version = "99.0.0"
    for subdir in release_packages.SUBDIRS:
        runtime_package(tmp_path, subdir, version)
    packages = release_packages.discover_packages(tmp_path, version)

    assert {package.package_name for package in packages} == {
        "conda-runtime",
        "conda-runtime-v2",
    }
    windows_v2 = tmp_path / "win-64" / release_packages.update_package_filename("win-64", version)
    windows_v2.rename(tmp_path / "win-64" / f"conda-runtime-{version}-0.conda")

    with pytest.raises(SystemExit, match="expected"):
        release_packages.discover_packages(tmp_path, version)


def test_windows_remote_checks_use_the_v2_package_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    package = runtime_package(tmp_path, "win-64")
    urls = []

    def fake_get_json(url, **_kwargs):
        urls.append(url)
        if "api.anaconda.org" in url:
            return api_metadata(package)
        return {
            "packages.conda": {
                package.filename: {
                    "name": package.package_name,
                    "version": package.version,
                    "build": "0",
                    "build_number": 0,
                    "subdir": package.subdir,
                    "sha256": package.sha256,
                    "size": package.size,
                }
            }
        }

    monkeypatch.setattr(release_packages, "get_json", fake_get_json)

    assert release_packages.api_has(package, "jezdez")
    assert release_packages.repodata_has(package, "jezdez")
    assert any("/package/jezdez/conda-runtime-v2" in url for url in urls)
