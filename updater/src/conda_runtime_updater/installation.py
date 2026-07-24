"""Identify package managers that own the outer conda executable."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path
from typing import TYPE_CHECKING

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import Any

    from .metadata import RuntimeMetadata


@dataclass(frozen=True)
class DetectedInstallation:
    """An external package manager installation proven by its receipt."""

    name: str
    executable: Path
    instruction: str | None = None


def detect_external_installation(runtime: RuntimeMetadata) -> DetectedInstallation | None:
    """Return the first supported package-manager receipt for the executable."""

    resolved = _resolve_file(runtime.executable)
    if resolved is None:
        return None

    return (
        _detect_homebrew(resolved)
        or _detect_uv_tool_receipt(resolved)
        or _detect_pipx_receipt(resolved)
        or _detect_python_wheel(runtime.executable, resolved)
        or _detect_reported_uv_tool(resolved)
        or _detect_reported_pipx(resolved)
        or _detect_global_pipx(resolved)
    )


def external_update_instruction(
    runtime: RuntimeMetadata,
    *,
    compatibility_instruction: str | None = None,
) -> str:
    """Return the update instruction for an externally managed runtime."""

    instructions = {
        "homebrew": (
            "This conda runtime is managed by Homebrew. Run brew update && brew upgrade conda, "
            "then retry conda self update."
        ),
        "pipx": _pipx_update_instruction(global_install=False),
        "uv-tool": (
            "This conda runtime is managed as a uv tool. Run uv tool upgrade conda-runtime, "
            "then retry conda self update."
        ),
        "pip": (
            "This conda runtime was installed by pip. Update conda-runtime in the Python "
            "environment that owns this executable, then retry conda self update."
        ),
        "uv-pip": (
            "This conda runtime was installed by uv pip. Update conda-runtime in the Python "
            "environment that owns this executable, then retry conda self update."
        ),
        "python": (
            "This conda runtime is managed by a Python package manager. Update conda-runtime "
            "with the Python package manager that installed it, then retry conda self update."
        ),
    }
    if runtime.instruction is not None:
        return runtime.instruction
    if instruction := instructions.get(runtime.installation):
        return instruction
    if compatibility_instruction is not None:
        return compatibility_instruction
    return (
        "Update the conda executable with the package manager that installed it, then retry "
        "conda self update."
    )


def _detect_homebrew(resolved: Path) -> DetectedInstallation | None:
    for directory in resolved.parents:
        receipt_path = directory / "INSTALL_RECEIPT.json"
        data = _read_json_mapping(receipt_path)
        if data is None:
            continue
        if directory.parent.name != "conda" or directory.parent.parent.name != "Cellar":
            continue
        if not isinstance(data.get("homebrew_version"), str):
            continue

        prefix = directory.parent.parent.parent
        linked = prefix / "bin" / resolved.name
        if linked.is_symlink() and _resolve_file(linked) == resolved:
            return DetectedInstallation(name="homebrew", executable=linked.absolute())

        opt = prefix / "opt" / "conda"
        stable = opt / "bin" / resolved.name
        if (
            opt.is_symlink()
            and _resolve_directory(opt) == directory
            and _resolve_file(stable) == resolved
        ):
            return DetectedInstallation(name="homebrew", executable=stable.absolute())
    return None


def _detect_uv_tool_receipt(resolved: Path) -> DetectedInstallation | None:
    for directory in resolved.parents:
        if (
            detected := _uv_receipt_installation(directory / "uv-receipt.toml", resolved)
        ) is not None:
            return detected
    return None


def _detect_reported_uv_tool(resolved: Path) -> DetectedInstallation | None:
    tool_dir = _reported_directory("uv", "tool", "dir")
    if tool_dir is None:
        return None
    return _uv_receipt_installation(
        tool_dir / "conda-runtime" / "uv-receipt.toml",
        resolved,
    )


def _uv_receipt_installation(
    receipt_path: Path,
    resolved: Path,
) -> DetectedInstallation | None:
    try:
        with receipt_path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    tool = data.get("tool") if isinstance(data, dict) else None
    if not isinstance(tool, dict) or not _uv_receipt_targets_conda_runtime(tool):
        return None

    entrypoints = tool.get("entrypoints")
    if not isinstance(entrypoints, list):
        return None
    for entrypoint in entrypoints:
        if not isinstance(entrypoint, dict) or entrypoint.get("name") != "conda":
            continue
        source = entrypoint.get("from")
        if not isinstance(source, str) or _distribution_name(source) != "conda-runtime":
            continue
        install_path = entrypoint.get("install-path")
        if not isinstance(install_path, str):
            continue
        executable = Path(install_path)
        if not executable.is_absolute() or _resolve_file(executable) != resolved:
            continue
        return DetectedInstallation(name="uv-tool", executable=executable)
    return None


def _uv_receipt_targets_conda_runtime(data: Mapping[str, Any]) -> bool:
    requirements = data.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return False
    requirement = requirements[0]
    if isinstance(requirement, str):
        name = requirement
    elif isinstance(requirement, dict) and isinstance(requirement.get("name"), str):
        name = requirement["name"]
    else:
        return False
    return _distribution_name(name) == "conda-runtime"


def _detect_pipx_receipt(resolved: Path) -> DetectedInstallation | None:
    for directory in resolved.parents:
        data = _read_json_mapping(directory / "pipx_metadata.json")
        if data is None:
            continue
        if detected := _detect_reported_pipx(resolved):
            return detected
        if detected := _detect_global_pipx(resolved):
            return detected
        if detected := _pipx_receipt_installation(
            data,
            resolved,
            instruction=_pipx_update_instruction(global_install=None),
        ):
            return detected
    return None


def _detect_reported_pipx(resolved: Path) -> DetectedInstallation | None:
    home = _reported_directory("pipx", "environment", "--value", "PIPX_HOME")
    bin_dir = _reported_directory("pipx", "environment", "--value", "PIPX_BIN_DIR")
    if home is None or bin_dir is None:
        return None
    data = _read_json_mapping(home / "venvs" / "conda-runtime" / "pipx_metadata.json")
    if data is None:
        return None
    stable = bin_dir / resolved.name
    return _pipx_receipt_installation(data, resolved, stable=stable)


def _detect_global_pipx(resolved: Path) -> DetectedInstallation | None:
    if os.name == "nt":
        return None
    home = Path(os.environ.get("PIPX_GLOBAL_HOME") or "/opt/pipx").expanduser().absolute()
    bin_dir = (
        Path(os.environ.get("PIPX_GLOBAL_BIN_DIR") or "/usr/local/bin").expanduser().absolute()
    )
    data = _read_json_mapping(home / "venvs" / "conda-runtime" / "pipx_metadata.json")
    if data is None:
        return None
    return _pipx_receipt_installation(
        data,
        resolved,
        stable=bin_dir / resolved.name,
        instruction=_pipx_update_instruction(global_install=True),
    )


def _pipx_receipt_installation(
    data: Mapping[str, Any],
    resolved: Path,
    *,
    stable: Path | None = None,
    instruction: str | None = None,
) -> DetectedInstallation | None:
    main_package = data.get("main_package")
    if not isinstance(main_package, dict):
        return None
    package = main_package.get("package")
    if not isinstance(package, str) or _distribution_name(package) != "conda-runtime":
        return None
    app_paths = main_package.get("app_paths")
    if not isinstance(app_paths, list):
        return None
    for value in app_paths:
        executable = _pipx_path(value)
        if executable is None or executable.name.casefold() not in {"conda", "conda.exe"}:
            continue
        if (
            stable is not None
            and stable.name.casefold() in {"conda", "conda.exe"}
            and _resolve_file(stable) == resolved
            and _same_contents(stable, executable)
        ):
            return DetectedInstallation(
                name="pipx",
                executable=stable.absolute(),
                instruction=instruction,
            )
        if _resolve_file(executable) == resolved:
            return DetectedInstallation(
                name="pipx",
                executable=executable,
                instruction=instruction,
            )
    return None


def _pipx_path(value: object) -> Path | None:
    if isinstance(value, str):
        path = value
    elif (
        isinstance(value, dict)
        and value.get("__type__") == "Path"
        and isinstance(value.get("__Path__"), str)
    ):
        path = value["__Path__"]
    else:
        return None
    executable = Path(path)
    return executable if executable.is_absolute() else None


def _pipx_update_instruction(*, global_install: bool | None) -> str:
    if global_install is True:
        command = "pipx upgrade --global conda-runtime"
        scope = "global pipx"
    elif global_install is False:
        command = "pipx upgrade conda-runtime"
        scope = "pipx"
    else:
        return (
            "This conda runtime is managed by pipx. Run pipx upgrade conda-runtime for a user "
            "installation or pipx upgrade --global conda-runtime for a global installation, "
            "then retry conda self update."
        )
    return (
        f"This conda runtime is managed by {scope}. Run {command}, then retry conda self update."
    )


def _detect_python_wheel(
    recorded: Path,
    resolved: Path,
) -> DetectedInstallation | None:
    for site_packages in _site_package_directories(recorded, resolved):
        for dist_info in site_packages.glob("conda_runtime-*.dist-info"):
            if not _is_conda_runtime_dist_info(dist_info):
                continue
            executable = _recorded_executable(dist_info / "RECORD", resolved)
            if executable is None:
                continue
            installer = _read_text(dist_info / "INSTALLER")
            if installer is not None and installer.strip().casefold() == "uv":
                name = "uv-pip"
            elif installer is not None and installer.strip().casefold() == "pip":
                name = "pip"
            else:
                name = "python"
            return DetectedInstallation(
                name=name,
                executable=executable,
                instruction=_wheel_update_instruction(
                    name,
                    executable=executable,
                    site_packages=site_packages,
                ),
            )
    return None


def _site_package_directories(recorded: Path, resolved: Path) -> Iterable[Path]:
    roots = []
    for executable in (recorded, resolved):
        parent = executable.absolute().parent
        if parent.name.casefold() in {"bin", "scripts"}:
            root = parent.parent
            if root not in roots:
                roots.append(root)

    seen = set()
    for root in roots:
        candidates = [
            root / "Lib" / "site-packages",
            root / "site-packages",
            *sorted((root / "lib").glob("python*/site-packages")),
            *sorted((root / "lib").glob("python*/dist-packages")),
            *sorted((root / "lib64").glob("python*/site-packages")),
        ]
        for candidate in candidates:
            key = os.path.normcase(str(candidate))
            if key in seen or not candidate.is_dir():
                continue
            seen.add(key)
            yield candidate


def _is_conda_runtime_dist_info(dist_info: Path) -> bool:
    metadata = _read_text(dist_info / "METADATA")
    if metadata is None:
        return False
    try:
        name = Parser().parsestr(metadata).get("Name")
    except (TypeError, UnicodeError):
        return False
    return isinstance(name, str) and _distribution_name(name) == "conda-runtime"


def _recorded_executable(record_path: Path, resolved: Path) -> Path | None:
    try:
        with record_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream))
    except (OSError, UnicodeError, csv.Error):
        return None

    site_packages = record_path.parent.parent
    for row in rows:
        if len(row) != 3:
            continue
        relative_path, encoded_digest, size_text = row
        if not encoded_digest.startswith("sha256=") or not size_text.isdecimal():
            continue
        executable = Path(os.path.normpath(site_packages / Path(relative_path)))
        if _resolve_file(executable) != resolved:
            continue
        try:
            if executable.stat().st_size != int(size_text):
                continue
        except OSError:
            continue
        try:
            digest = _sha256(executable)
        except OSError:
            continue
        actual = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        if actual == encoded_digest.removeprefix("sha256="):
            return executable
    return None


def _distribution_name(value: str) -> str:
    name = value.strip().split("[", 1)[0]
    for marker in (" ", "=", "<", ">", "!", "~", "@"):
        name = name.split(marker, 1)[0]
    return name.strip().casefold().replace("_", "-").replace(".", "-")


def _read_json_mapping(path: Path) -> Mapping[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _same_contents(left: Path, right: Path) -> bool:
    try:
        return left.stat().st_size == right.stat().st_size and _sha256(left) == _sha256(right)
    except OSError:
        return False


def _wheel_update_instruction(
    installation: str,
    *,
    executable: Path,
    site_packages: Path,
) -> str | None:
    owner = _owning_python(executable, site_packages)
    if owner is None:
        return None
    if installation == "pip":
        command = _display_command(
            [str(owner), "-m", "pip", "install", "--upgrade", "conda-runtime"]
        )
        manager = "pip"
    elif installation == "uv-pip":
        command = _display_command(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(owner),
                "--upgrade",
                "conda-runtime",
            ]
        )
        manager = "uv pip"
    else:
        return None
    return (
        f"This conda runtime is managed by {manager}. Run {command}, then retry conda self update."
    )


def _owning_python(executable: Path, site_packages: Path) -> Path | None:
    scripts = executable.absolute().parent
    root = scripts.parent
    if not (root / "pyvenv.cfg").is_file():
        return None
    if scripts.name.casefold() == "scripts":
        if site_packages != root / "Lib" / "site-packages":
            return None
        candidates = (scripts / "python.exe", root / "python.exe")
    elif scripts.name.casefold() == "bin":
        try:
            relative = site_packages.relative_to(root)
        except ValueError:
            return None
        if not relative.parts or relative.parts[0].casefold() not in {"lib", "lib64"}:
            return None
        candidates = (scripts / "python", scripts / "python3")
    else:
        return None
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _display_command(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def _reported_directory(*arguments: str) -> Path | None:
    executable = shutil.which(arguments[0])
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, *arguments[1:]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 1:
        return None
    path = Path(lines[0].strip())
    return path if path.is_absolute() and path.is_dir() else None


def _resolve_file(path: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def _resolve_directory(path: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None
