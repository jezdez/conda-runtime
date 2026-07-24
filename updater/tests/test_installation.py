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

HOMEBREW_INSTRUCTION = (
    "This conda runtime is managed by Homebrew. Run brew update && brew upgrade conda, "
    "then retry conda self update."
)
UV_TOOL_INSTRUCTION = (
    "This conda runtime is managed as a uv tool. Run uv tool upgrade conda-runtime, "
    "then retry conda self update."
)
PIPX_INSTRUCTION = (
    "This conda runtime is managed by pipx. Run pipx upgrade conda-runtime for a user "
    "installation or pipx upgrade --global conda-runtime for a global installation, "
    "then retry conda self update."
)


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
) -> Path:
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
    return dist_info


def homebrew_keg(tmp_path):
    prefix = tmp_path / "homebrew"
    keg = prefix / "Cellar" / "conda" / "26.5.3"
    executable = keg / "bin" / "conda"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda")
    (keg / "INSTALL_RECEIPT.json").write_text(
        json.dumps({"homebrew_version": "5.0.0"}),
        encoding="utf-8",
    )
    return prefix, keg, executable


def write_uv_receipt(
    tool: Path,
    executable: Path,
    *,
    requirements: str = '[{ name = "conda-runtime" }]',
    source: str = "conda-runtime",
) -> None:
    tool.mkdir(parents=True, exist_ok=True)
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


def write_pipx_receipt(tool: Path, executable: Path, *, encoded_path: bool = False) -> None:
    app_path: str | dict[str, str]
    if encoded_path:
        app_path = {"__type__": "Path", "__Path__": str(executable)}
    else:
        app_path = str(executable)
    (tool / "pipx_metadata.json").write_text(
        json.dumps(
            {
                "pipx_metadata_version": "0.12",
                "main_package": {
                    "package": "conda-runtime",
                    "apps": ["conda"],
                    "app_paths": [app_path],
                },
            }
        ),
        encoding="utf-8",
    )


def wheel_installation(tmp_path, *, installer: str | None, with_owner: bool = False):
    scripts = tmp_path / ("Scripts" if os.name == "nt" else "bin")
    executable = scripts / ("conda.exe" if os.name == "nt" else "conda")
    executable.parent.mkdir()
    executable.write_bytes(b"conda runtime")
    site_packages = (
        tmp_path / "Lib" / "site-packages"
        if os.name == "nt"
        else tmp_path / "lib" / "python3.12" / "site-packages"
    )
    owner = scripts / ("python.exe" if os.name == "nt" else "python")
    if with_owner:
        owner.write_bytes(b"python")
        (tmp_path / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
    write_wheel_receipt(executable, site_packages, installer=installer)
    return executable, site_packages, owner


@pytest.mark.skipif(os.name == "nt", reason="Homebrew uses POSIX symlinks")
def test_detects_homebrew_keg_and_returns_stable_symlink(tmp_path):
    prefix, keg, executable = homebrew_keg(tmp_path)
    stable = prefix / "bin" / "conda"
    stable.parent.mkdir()
    stable.symlink_to(executable)
    opt = prefix / "opt"
    opt.mkdir()
    (opt / "conda").symlink_to(keg, target_is_directory=True)

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(
        name="homebrew",
        executable=stable,
        instruction=HOMEBREW_INSTRUCTION,
    )


def test_homebrew_receipt_without_stable_symlink_is_not_accepted(tmp_path):
    _prefix, _keg, executable = homebrew_keg(tmp_path)

    assert detect_external_installation(runtime_for(executable)) is None


@pytest.mark.skipif(os.name == "nt", reason="Homebrew uses POSIX symlinks")
def test_detects_homebrew_opt_link_when_main_link_is_absent(tmp_path):
    prefix, keg, _executable = homebrew_keg(tmp_path)
    opt = prefix / "opt"
    opt.mkdir()
    (opt / "conda").symlink_to(keg, target_is_directory=True)

    detected = detect_external_installation(runtime_for(opt / "conda" / "bin" / "conda"))

    assert detected == DetectedInstallation(
        name="homebrew",
        executable=opt / "conda" / "bin" / "conda",
        instruction=HOMEBREW_INSTRUCTION,
    )


def test_detects_uv_tool_receipt(tmp_path):
    tool = tmp_path / "uv" / "tools" / "conda-runtime"
    executable = tool / ("Scripts/conda.exe" if os.name == "nt" else "bin/conda")
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda")
    write_uv_receipt(tool, executable)
    write_pipx_receipt(tool, executable)

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(
        name="uv-tool",
        executable=executable,
        instruction=UV_TOOL_INSTRUCTION,
    )


def test_detects_copied_uv_tool_executable_from_reported_tool_dir(monkeypatch, tmp_path):
    tool_dir = tmp_path / "uv" / "tools"
    receipt = tool_dir / "conda-runtime" / "uv-receipt.toml"
    receipt.parent.mkdir(parents=True)
    executable = tmp_path / "uv" / "bin" / ("conda.exe" if os.name == "nt" else "conda")
    executable.parent.mkdir()
    executable.write_bytes(b"conda")
    write_uv_receipt(receipt.parent, executable)
    monkeypatch.setattr(
        installation,
        "_reported_directory",
        lambda *arguments: tool_dir if arguments == ("uv", "tool", "dir") else None,
    )

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(
        name="uv-tool",
        executable=executable,
        instruction=UV_TOOL_INSTRUCTION,
    )


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
    write_uv_receipt(tool, executable, requirements=requirements, source=source)

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
    write_pipx_receipt(tool, executable, encoded_path=True)

    detected = detect_external_installation(runtime_for(executable))

    assert detected == DetectedInstallation(
        name="pipx",
        executable=executable,
        instruction=PIPX_INSTRUCTION,
    )


@pytest.mark.skipif(os.name == "nt", reason="pipx uses a POSIX symlink")
@pytest.mark.parametrize(
    ("global_install", "instruction"),
    [
        (
            False,
            "This conda runtime is managed by pipx. Run pipx upgrade conda-runtime, "
            "then retry conda self update.",
        ),
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
    write_pipx_receipt(tool, internal)
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
    write_pipx_receipt(internal.parents[1], internal, encoded_path=True)
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

    assert detected == DetectedInstallation(
        name="pipx",
        executable=stable,
        instruction=(
            "This conda runtime is managed by pipx. Run pipx upgrade conda-runtime, "
            "then retry conda self update."
        ),
    )


def test_rejects_changed_pipx_exposed_copy(monkeypatch, tmp_path):
    home = tmp_path / "pipx"
    internal = home / "venvs" / "conda-runtime" / "bin" / "conda"
    internal.parent.mkdir(parents=True)
    internal.write_bytes(b"internal")
    write_pipx_receipt(internal.parents[1], internal)
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
    executable, _site_packages, _owner = wheel_installation(tmp_path, installer=installer)

    detected = detect_external_installation(runtime_for(executable))

    assert detected is not None
    assert detected.name == expected
    assert detected.executable == executable
    assert detected.instruction


def test_python_wheel_requires_record_hash_and_size(tmp_path):
    executable = tmp_path / "bin" / "conda"
    executable.parent.mkdir()
    executable.write_bytes(b"conda runtime")
    site_packages = tmp_path / "lib" / "python3.12" / "site-packages"
    write_wheel_receipt(executable, site_packages, valid_record=False)

    assert detect_external_installation(runtime_for(executable)) is None


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("METADATA", b"\xff"),
        ("RECORD", b"../../bin/conda,sha256=invalid,not-a-size\n"),
        ("INSTALLER", b"\xff"),
    ],
)
def test_python_wheel_malformed_receipt_fails_closed(tmp_path, filename, content):
    executable, site_packages, _owner = wheel_installation(tmp_path, installer="pip")
    dist_info = site_packages / "conda_runtime-26.5.3.dist-info"
    (dist_info / filename).write_bytes(content)

    assert detect_external_installation(runtime_for(executable)) is None


@pytest.mark.parametrize(
    ("installer", "expected"),
    [
        ("pip", "-m pip install --upgrade conda-runtime"),
        ("uv", "uv pip install --python"),
    ],
)
def test_python_wheel_instruction_targets_owning_interpreter(tmp_path, installer, expected):
    executable, _site_packages, owner = wheel_installation(
        tmp_path,
        installer=installer,
        with_owner=True,
    )

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

    assert detected is not None
    assert detected.name == "pip"
    assert detected.executable == executable
    assert "installed by pip" in detected.instruction


def test_detects_windows_user_scheme_with_generic_instruction(tmp_path):
    root = tmp_path / "Python312"
    executable = root / "Scripts" / "conda.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"conda runtime")
    site_packages = root / "site-packages"
    write_wheel_receipt(executable, site_packages, installer="pip")

    detected = detect_external_installation(runtime_for(executable))

    assert detected is not None
    assert detected.name == "pip"
    assert detected.executable == executable
    assert "installed by pip" in detected.instruction


@pytest.mark.parametrize(
    "outcome",
    [
        SimpleNamespace(returncode=1, stdout="/absolute/path\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="relative/path\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="/one\n/two\n", stderr=""),
        subprocess.TimeoutExpired(["uv", "tool", "dir"], 5),
        OSError("command not found"),
    ],
)
def test_reported_directory_fails_closed(monkeypatch, outcome):
    def run(*_args, **_kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(installation.subprocess, "run", run)

    assert installation._reported_directory("uv", "tool", "dir") is None


def test_external_instruction_uses_persisted_instruction(tmp_path):
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


def test_external_instruction_uses_helper_compatibility_fallback(tmp_path):
    executable = tmp_path / "conda"
    executable.write_bytes(b"conda")
    runtime = runtime_for(executable, installation="old-installer")

    assert (
        external_update_instruction(
            runtime,
            compatibility_instruction="Run the compatible updater.",
        )
        == "Run the compatible updater."
    )
