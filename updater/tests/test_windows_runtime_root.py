from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import tomllib

SCRIPT_PATH = Path(__file__).parents[2] / "scripts/prepare_windows_runtime_root.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("prepare_windows_runtime_root", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
windows_root = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = windows_root
SPEC.loader.exec_module(windows_root)


def source_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "conda.lock").write_bytes(b"exact lock bytes\n")
    (root / "runtime.condarc").write_bytes(b"channels: []\n")
    (root / "conda.toml").write_text(
        """\
[tool.conda-ship]
runtime-version = "26.7.1.post2"

[tool.conda-ship.update]
channel = "https://conda.anaconda.org/jezdez"
package = "conda-runtime"
build-number = 0
""",
        encoding="utf-8",
    )
    return root


def test_derived_root_changes_only_the_windows_package(tmp_path: Path):
    source = source_root(tmp_path)
    destination = tmp_path / "windows"

    result = windows_root.prepare_windows_runtime_root(source, destination)

    assert result == destination.resolve()
    assert (destination / "conda.lock").read_bytes() == (source / "conda.lock").read_bytes()
    assert (destination / "runtime.condarc").read_bytes() == (
        source / "runtime.condarc"
    ).read_bytes()
    source_manifest = tomllib.loads((source / "conda.toml").read_text())
    derived_manifest = tomllib.loads((destination / "conda.toml").read_text())
    assert source_manifest["tool"]["conda-ship"]["update"].pop("package") == ("conda-runtime")
    assert derived_manifest["tool"]["conda-ship"]["update"].pop("package") == ("conda-runtime-v2")
    assert derived_manifest == source_manifest


def test_derived_root_rejects_unexpected_source_file(tmp_path: Path):
    source = source_root(tmp_path)
    (source / "untracked.txt").write_text("unexpected", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain"):
        windows_root.prepare_windows_runtime_root(source, tmp_path / "windows")


def test_derived_root_requires_the_legacy_package(tmp_path: Path):
    source = source_root(tmp_path)
    manifest = source / "conda.toml"
    manifest.write_text(
        manifest.read_text().replace("conda-runtime", "other-package"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="legacy conda-runtime"):
        windows_root.prepare_windows_runtime_root(source, tmp_path / "windows")
