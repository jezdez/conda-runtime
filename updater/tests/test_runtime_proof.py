from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts/prove-runtime-update.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("prove_runtime_update", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
runtime_proof = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime_proof
SPEC.loader.exec_module(runtime_proof)


def test_runtime_proof_disables_user_always_yes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv("CONDA_ALWAYS_YES", "true")
    scenario = runtime_proof.Scenario(
        root=tmp_path,
        prefix=tmp_path / "prefix",
        stable=tmp_path / "bin/conda",
        envs=tmp_path / "envs",
        packages=tmp_path / "packages",
        platform="osx-arm64",
    )

    environment = runtime_proof.runtime_environment(scenario)

    assert environment["CONDA_ALWAYS_YES"] == "false"


@pytest.mark.parametrize("platform", ["win-64", "win-arm64"])
def test_windows_update_scenario_preserves_executable_suffix(tmp_path: Path, platform: str):
    source = tmp_path / "source.exe"
    source.write_bytes(b"native runtime")

    scenario = runtime_proof.new_scenario(tmp_path / "scenario", source, platform)

    assert scenario.stable.name == "conda.exe"
    assert scenario.stable.read_bytes() == source.read_bytes()
