from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from conda_runtime_updater import installation
from conda_runtime_updater.installation import (
    DetectedInstallation,
    detect_external_installation,
    external_update_instruction,
)
from conda_runtime_updater.metadata import RuntimeMetadata


@pytest.fixture(autouse=True)
def disable_package_manager_commands(monkeypatch):
    monkeypatch.setattr(installation, "_reported_directory", lambda *_arguments: None)


def runtime_for(executable: Path, *, installation: str | None = None) -> RuntimeMetadata:
    prefix = executable.parent / "prefix"
    return RuntimeMetadata(
        prefix=prefix,
        path=prefix / ".conda.json",
        version="26.5.3",
        executable=executable,
        lock_path=prefix / ".conda.update.lock",
        ownership="external" if installation is not None else "direct",
        installation=installation,
        instruction=None,
    )


def write_wheel_receipt(
    executable: Path,
    site_packages: Path,
    *,
    installer: str | None = None,
    valid_record: bool = True,
) -> None:
    dist_info = site_packages / "conda_runtime-26.5.3.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: conda-runtime\nVersion: 26.5.3\n",
        encoding="utf-8",
    )
    if installer is not None:
        (dist_info / "INSTALLER").write_text(f"{installer}\n", encoding="utf-8")
    relative = Path(os.path.relpath(executable, site_packages)).as_posix()
    if valid_record:
        digest = base64.urlsafe_b64encode(hashlib.sha256(executable.read_bytes()).digest())
        encoded_digest = digest.rstrip(b"=").decode("ascii")
        record = f"{relative},sha256={encoded_digest},{executable.stat().st_size}\n"
    else:
        record = f"{relative},,\n"
    (dist_info / "RECORD").write_text(record, encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="Homebrew uses POSIX symlinks")
def test_detects_homebrew_keg_and_returns_stable_symlink(tmp_path):
    keg = tmp_path / "homebrew" / "Cellar" / "conda" / "26.5.3"
    executable = keg / "bin" / "conda"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda")
    (keg / "INSTALL_RECEIPT.json").write_text(
        json.dumps({"homebrew_version": "5.0.0"}),
        encoding="utf-8",
    )
    stable = tmp_path / "homebrew" / "bin" / "conda"
    stable.parent.mkdir()
    stable.symlink_to(executable)
    opt = tmp_path / "homebrew" / "opt"
    opt.mkdir()
    (opt / "conda").symlink_to(keg, target_is_directory=True)

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(name="homebrew", executable=stable)


def test_homebrew_receipt_without_stable_symlink_is_not_accepted(tmp_path):
    keg = tmp_path / "homebrew" / "Cellar" / "conda" / "26.5.3"
    executable = keg / "bin" / "conda"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda")
    (keg / "INSTALL_RECEIPT.json").write_text(
        json.dumps({"homebrew_version": "5.0.0"}),
        encoding="utf-8",
    )

    assert detect_external_installation(runtime_for(executable)) is None


@pytest.mark.skipif(os.name == "nt", reason="Homebrew uses POSIX symlinks")
def test_detects_homebrew_opt_link_when_main_link_is_absent(tmp_path):
    prefix = tmp_path / "homebrew"
    keg = prefix / "Cellar" / "conda" / "26.5.3"
    executable = keg / "bin" / "conda"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda")
    (keg / "INSTALL_RECEIPT.json").write_text(
        json.dumps({"homebrew_version": "5.0.0"}),
        encoding="utf-8",
    )
    opt = prefix / "opt"
    opt.mkdir()
    (opt / "conda").symlink_to(keg, target_is_directory=True)

    detected = detect_external_installation(runtime_for(opt / "conda" / "bin" / "conda"))

    assert detected == DetectedInstallation(
        name="homebrew",
        executable=opt / "conda" / "bin" / "conda",
    )


def test_detects_uv_tool_receipt(tmp_path):
    tool = tmp_path / "uv" / "tools" / "conda-runtime"
    executable = tool / ("Scripts/conda.exe" if os.name == "nt" else "bin/conda")
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda")
    install_path = json.dumps(str(executable))
    (tool / "uv-receipt.toml").write_text(
        "\n".join(
            (
                "[tool]",
                'requirements = [{ name = "conda-runtime" }]',
                "entrypoints = [",
                (
                    f'  {{ name = "conda", install-path = {install_path}, '
                    'from = "conda-runtime" },'
                ),
                "]",
            )
        ),
        encoding="utf-8",
    )
    (tool / "pipx_metadata.json").write_text(
        json.dumps(
            {
                "main_package": {
                    "package": "conda-runtime",
                    "app_paths": [str(executable)],
                }
            }
        ),
        encoding="utf-8",
    )

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(name="uv-tool", executable=executable)


def test_detects_copied_uv_tool_executable_from_reported_tool_dir(monkeypatch, tmp_path):
    tool_dir = tmp_path / "uv" / "tools"
    receipt = tool_dir / "conda-runtime" / "uv-receipt.toml"
    receipt.parent.mkdir(parents=True)
    executable = tmp_path / "uv" / "bin" / ("conda.exe" if os.name == "nt" else "conda")
    executable.parent.mkdir()
    executable.write_bytes(b"conda")
    receipt.write_text(
        "\n".join(
            (
                "[tool]",
                'requirements = [{ name = "conda-runtime" }]',
                "entrypoints = [",
                (
                    f'  {{ name = "conda", install-path = {json.dumps(str(executable))}, '
                    'from = "conda-runtime" },'
                ),
                "]",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        installation,
        "_reported_directory",
        lambda *arguments: tool_dir if arguments == ("uv", "tool", "dir") else None,
    )

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(name="uv-tool", executable=executable)


@pytest.mark.parametrize(
    ("requirements", "source"),
    (
        (
            '[{ name = "other" }, { name = "conda-runtime" }]',
            "conda-runtime",
        ),
        (
            '[{ name = "conda-runtime" }]',
            "other",
        ),
    ),
)
def test_uv_tool_receipt_binds_main_requirement_and_entrypoint(tmp_path, requirements, source):
    tool = tmp_path / "uv" / "tools" / "other"
    executable = tool / ("Scripts/conda.exe" if os.name == "nt" else "bin/conda")
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda")
    (tool / "uv-receipt.toml").write_text(
        "\n".join(
            (
                "[tool]",
                f"requirements = {requirements}",
                "entrypoints = [",
                (
                    f'  {{ name = "conda", install-path = {json.dumps(str(executable))}, '
                    f'from = "{source}" }},'
                ),
                "]",
            )
        ),
        encoding="utf-8",
    )

    assert detect_external_installation(runtime_for(executable)) is None


def test_rejects_obsolete_top_level_uv_receipt(tmp_path):
    tool = tmp_path / "uv" / "tools" / "conda-runtime"
    executable = tool / ("Scripts/conda.exe" if os.name == "nt" else "bin/conda")
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda")
    (tool / "uv-receipt.toml").write_text(
        f'requirements = ["conda-runtime"]\n'
        f'entrypoints = [{{ name = "conda", install-path = {json.dumps(str(executable))} }}]\n',
        encoding="utf-8",
    )

    assert detect_external_installation(runtime_for(executable)) is None


def test_detects_pipx_receipt(tmp_path):
    tool = tmp_path / "pipx" / "venvs" / "conda-runtime"
    executable = tool / ("Scripts/conda.exe" if os.name == "nt" else "bin/conda")
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda")
    (tool / "pipx_metadata.json").write_text(
        json.dumps(
            {
                "pipx_metadata_version": "0.12",
                "main_package": {
                    "package": "conda-runtime",
                    "apps": ["conda"],
                    "app_paths": [
                        {
                            "__type__": "Path",
                            "__Path__": str(executable),
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(
        name="pipx",
        executable=executable,
        instruction=(
            "This conda runtime is managed by pipx. Run pipx upgrade conda-runtime for a user "
            "installation or pipx upgrade --global conda-runtime for a global installation, "
            "then retry conda self update."
        ),
    )


@pytest.mark.skipif(os.name == "nt", reason="pipx uses a POSIX symlink")
@pytest.mark.parametrize(
    ("global_install", "instruction"),
    [
        (False, None),
        (
            True,
            "This conda runtime is managed by global pipx. Run "
            "pipx upgrade --global conda-runtime, then retry conda self update.",
        ),
    ],
    ids=("user", "global"),
)
def test_pipx_receipt_prefers_reported_exposed_symlink(
    monkeypatch,
    tmp_path,
    global_install,
    instruction,
):
    home = tmp_path / "pipx"
    tool = home / "venvs" / "conda-runtime"
    internal = tool / "bin" / "conda"
    internal.parent.mkdir(parents=True)
    internal.write_bytes(b"conda")
    (tool / "pipx_metadata.json").write_text(
        json.dumps(
            {
                "main_package": {
                    "package": "conda-runtime",
                    "app_paths": [str(internal)],
                }
            }
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stable = bin_dir / "conda"
    stable.symlink_to(internal)
    if global_install:
        monkeypatch.setenv("PIPX_GLOBAL_HOME", str(home))
        monkeypatch.setenv("PIPX_GLOBAL_BIN_DIR", str(bin_dir))

    queries = []

    def reported(*arguments):
        queries.append(arguments)
        if global_install:
            return None
        if arguments[-1] == "PIPX_HOME":
            return home
        if arguments[-1] == "PIPX_BIN_DIR":
            return bin_dir
        return None

    monkeypatch.setattr(installation, "_reported_directory", reported)

    detected = detect_external_installation(runtime_for(internal))

    assert detected == DetectedInstallation(
        name="pipx",
        executable=stable,
        instruction=instruction,
    )
    assert all("--global" not in query for query in queries)


def test_global_pipx_detection_is_disabled_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(installation.os, "name", "nt")

    assert installation._detect_global_pipx(tmp_path / "conda.exe") is None


def test_detects_copied_pipx_executable_from_reported_paths(monkeypatch, tmp_path):
    home = tmp_path / "pipx"
    internal = (
        home
        / "venvs"
        / "conda-runtime"
        / ("Scripts/conda.exe" if os.name == "nt" else "bin/conda")
    )
    internal.parent.mkdir(parents=True)
    internal.write_bytes(b"conda")
    (internal.parents[1] / "pipx_metadata.json").write_text(
        json.dumps(
            {
                "pipx_metadata_version": "0.12",
                "main_package": {
                    "package": "conda-runtime",
                    "apps": ["conda"],
                    "app_paths": [
                        {
                            "__type__": "Path",
                            "__Path__": str(internal),
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stable = bin_dir / internal.name
    stable.write_bytes(internal.read_bytes())

    def reported(*arguments):
        if arguments[-1] == "PIPX_HOME":
            return home
        if arguments[-1] == "PIPX_BIN_DIR":
            return bin_dir
        return None

    monkeypatch.setattr(installation, "_reported_directory", reported)

    detected = detect_external_installation(runtime_for(stable))

    assert detected == DetectedInstallation(name="pipx", executable=stable)


def test_rejects_changed_pipx_exposed_copy(monkeypatch, tmp_path):
    home = tmp_path / "pipx"
    internal = home / "venvs" / "conda-runtime" / "bin" / "conda"
    internal.parent.mkdir(parents=True)
    internal.write_bytes(b"internal")
    (internal.parents[1] / "pipx_metadata.json").write_text(
        json.dumps(
            {
                "main_package": {
                    "package": "conda-runtime",
                    "app_paths": [str(internal)],
                }
            }
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stable = bin_dir / "conda"
    stable.write_bytes(b"changed")
    monkeypatch.setattr(
        installation,
        "_reported_directory",
        lambda *arguments: home if arguments[-1] == "PIPX_HOME" else bin_dir,
    )

    assert detect_external_installation(runtime_for(stable)) is None


@pytest.mark.parametrize(
    ("installer", "expected"),
    [
        ("pip", "pip"),
        ("uv", "uv-pip"),
        ("other", "python"),
        (None, "python"),
    ],
)
def test_detects_record_bound_python_wheel(tmp_path, installer, expected):
    scripts = tmp_path / ("Scripts" if os.name == "nt" else "bin")
    executable = scripts / ("conda.exe" if os.name == "nt" else "conda")
    executable.parent.mkdir()
    executable.write_bytes(b"conda runtime")
    site_packages = (
        tmp_path / "Lib" / "site-packages"
        if os.name == "nt"
        else tmp_path / "lib" / "python3.12" / "site-packages"
    )
    write_wheel_receipt(executable, site_packages, installer=installer)

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(name=expected, executable=executable)


def test_python_wheel_requires_record_hash_and_size(tmp_path):
    executable = tmp_path / "bin" / "conda"
    executable.parent.mkdir()
    executable.write_bytes(b"conda runtime")
    site_packages = tmp_path / "lib" / "python3.12" / "site-packages"
    write_wheel_receipt(executable, site_packages, valid_record=False)

    assert detect_external_installation(runtime_for(executable)) is None


@pytest.mark.parametrize(
    ("installer", "expected"),
    [
        ("pip", "-m pip install --upgrade conda-runtime"),
        ("uv", "uv pip install --python"),
    ],
)
def test_python_wheel_instruction_targets_owning_interpreter(tmp_path, installer, expected):
    scripts = tmp_path / ("Scripts" if os.name == "nt" else "bin")
    executable = scripts / ("conda.exe" if os.name == "nt" else "conda")
    executable.parent.mkdir()
    executable.write_bytes(b"conda runtime")
    owner = scripts / ("python.exe" if os.name == "nt" else "python")
    owner.write_bytes(b"python")
    (tmp_path / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
    site_packages = (
        tmp_path / "Lib" / "site-packages"
        if os.name == "nt"
        else tmp_path / "lib" / "python3.12" / "site-packages"
    )
    write_wheel_receipt(executable, site_packages, installer=installer)

    detected = detect_external_installation(runtime_for(executable))

    assert detected is not None
    assert detected.instruction is not None
    assert str(owner) in detected.instruction
    assert expected in detected.instruction


def test_python_wheel_user_scheme_does_not_guess_adjacent_interpreter(tmp_path):
    executable = tmp_path / "bin" / "conda"
    executable.parent.mkdir()
    executable.write_bytes(b"conda runtime")
    (executable.parent / "python").write_bytes(b"unrelated python")
    site_packages = tmp_path / "lib" / "python3.12" / "site-packages"
    write_wheel_receipt(executable, site_packages, installer="pip")

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(name="pip", executable=executable)


def test_detects_windows_user_scheme_with_generic_instruction(tmp_path):
    root = tmp_path / "Python312"
    executable = root / "Scripts" / "conda.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda runtime")
    site_packages = root / "site-packages"
    write_wheel_receipt(executable, site_packages, installer="pip")

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(name="pip", executable=executable)


@pytest.mark.parametrize(
    "outcome",
    [
        SimpleNamespace(returncode=1, stdout="/absolute/path\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="relative/path\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="/one\n/two\n", stderr=""),
        subprocess.TimeoutExpired(["uv", "tool", "dir"], 5),
    ],
)
def test_reported_directory_fails_closed(monkeypatch, outcome):
    monkeypatch.setattr(installation.shutil, "which", lambda _name: "/usr/bin/uv")

    def run(*_args, **_kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(installation.subprocess, "run", run)

    assert installation._reported_directory("uv", "tool", "dir") is None


@pytest.mark.parametrize(
    ("installation", "command"),
    [
        ("homebrew", "brew update && brew upgrade conda"),
        ("pipx", "pipx upgrade conda-runtime"),
        ("uv-tool", "uv tool upgrade conda-runtime"),
        ("pip", "Python environment that owns this executable"),
        ("uv-pip", "Python environment that owns this executable"),
        ("python", "Python package manager that installed it"),
    ],
)
def test_external_instruction_matches_installation(tmp_path, installation, command):
    executable = tmp_path / "conda"
    executable.write_bytes(b"conda")
    runtime = runtime_for(executable, installation=installation)

    instruction = external_update_instruction(runtime)

    assert command in instruction
    assert "retry conda self update" in instruction


def test_downstream_instruction_is_fallback_for_unknown_installation(tmp_path):
    executable = tmp_path / "conda"
    executable.write_bytes(b"conda")
    runtime = RuntimeMetadata(
        prefix=tmp_path,
        path=tmp_path / ".conda.json",
        version="26.5.3",
        executable=executable,
        lock_path=tmp_path / ".conda.update.lock",
        ownership="external",
        installation="downstream",
        instruction="Run the downstream updater.",
    )

    assert external_update_instruction(runtime) == "Run the downstream updater."
