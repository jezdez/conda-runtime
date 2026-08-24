from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
