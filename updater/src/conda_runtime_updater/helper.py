"""Invoke and validate the stamped runtime's version-one update helper."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from conda.base.context import context
from conda.exceptions import CondaError

from .metadata import valid_installation

if TYPE_CHECKING:
    from typing import Any

    from .metadata import RuntimeMetadata

ACTION_ENV = "CONDA_SHIP_INTERNAL_UPDATE"
CANDIDATE_ENV = "CONDA_SHIP_INTERNAL_UPDATE_CANDIDATE"
OFFLINE_ENV = "CONDA_SHIP_INTERNAL_UPDATE_OFFLINE"
OWNERSHIP_ENV = "CONDA_SHIP_INTERNAL_UPDATE_OWNERSHIP"
INSTALLATION_ENV = "CONDA_SHIP_INTERNAL_UPDATE_INSTALLATION"
EXECUTABLE_ENV = "CONDA_SHIP_INTERNAL_UPDATE_EXECUTABLE"
INSTRUCTION_ENV = "CONDA_SHIP_INTERNAL_UPDATE_INSTRUCTION"
PREFIX_ENV = "CONDA_SHIP_PREFIX"


def invoke_helper(
    runtime: RuntimeMetadata,
    action: str,
    *,
    candidate: str | None = None,
    ownership: str | None = None,
    installation: str | None = None,
    executable: Path | None = None,
    instruction: str | None = None,
) -> dict[str, Any]:
    """Invoke one version-one action on the stamped outer executable."""

    env = os.environ.copy()
    env[PREFIX_ENV] = str(runtime.prefix)
    env[ACTION_ENV] = f"v1/{action}"
    env.pop(CANDIDATE_ENV, None)
    env.pop(OFFLINE_ENV, None)
    env.pop(OWNERSHIP_ENV, None)
    env.pop(INSTALLATION_ENV, None)
    env.pop(EXECUTABLE_ENV, None)
    env.pop(INSTRUCTION_ENV, None)
    if candidate is not None:
        env[CANDIDATE_ENV] = candidate
    if ownership is not None:
        env[OWNERSHIP_ENV] = ownership
    if installation is not None:
        env[INSTALLATION_ENV] = installation
    if executable is not None:
        env[EXECUTABLE_ENV] = str(executable)
    if instruction is not None:
        env[INSTRUCTION_ENV] = instruction
    if context.offline:
        env[OFFLINE_ENV] = "1"

    try:
        result = subprocess.run(
            [runtime.executable],
            capture_output=True,
            check=False,
            encoding="utf-8",
            env=env,
            timeout=600,
        )
    except subprocess.TimeoutExpired as error:
        raise CondaError(
            f"Standalone conda executable {action} timed out after 600 seconds."
        ) from error
    except OSError as error:
        raise CondaError(
            f"Could not start the standalone conda executable for {action}: {error}"
        ) from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown helper error"
        raise CondaError(f"Standalone conda executable {action} failed: {detail}")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CondaError(f"Standalone conda executable {action} returned invalid JSON.") from error
    if not isinstance(response, dict):
        raise CondaError(f"Standalone conda executable {action} returned invalid data.")
    return response


def validate_record_installation(
    response: dict[str, Any],
    *,
    ownership: str,
    installation: str,
    executable: Path,
    instruction: str | None,
) -> None:
    """Validate a record-installation response against the requested state."""

    if response.get("recorded") is not True:
        raise CondaError("Standalone conda executable did not record its installation.")
    if response.get("ownership") != ownership:
        raise CondaError("Standalone conda executable recorded the wrong ownership.")
    if response.get("installation") != installation:
        raise CondaError("Standalone conda executable recorded the wrong installation.")
    response_executable = response.get("executable")
    if not isinstance(response_executable, str) or Path(response_executable) != executable:
        raise CondaError("Standalone conda executable recorded the wrong executable path.")
    if response.get("instruction") != instruction:
        raise CondaError("Standalone conda executable recorded the wrong update instruction.")


def validate_check(
    response: dict[str, Any],
    runtime: RuntimeMetadata,
) -> dict[str, Any]:
    """Validate the check fields used by the coordinator."""

    if not isinstance(response.get("available"), bool):
        raise CondaError("Standalone conda executable check omitted update availability.")
    if response.get("ownership") != runtime.ownership:
        raise CondaError("Standalone conda executable ownership changed during the update check.")
    installation = response.get("installation")
    if installation is not None and not valid_installation(installation):
        raise CondaError("Standalone conda executable check returned an invalid installation.")
    if runtime.installation is not None and installation != runtime.installation:
        raise CondaError(
            "Standalone conda executable installation changed during the update check."
        )
    if not response["available"]:
        return response

    version = response.get("version")
    digest = response.get("sha256")
    build_number = response.get("build_number")
    instruction = response.get("instruction")
    if not isinstance(version, str) or not version:
        raise CondaError("Standalone conda executable check omitted the candidate version.")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise CondaError("Standalone conda executable check returned an invalid SHA-256 digest.")
    if not isinstance(build_number, int) or isinstance(build_number, bool) or build_number < 0:
        raise CondaError("Standalone conda executable check returned an invalid build number.")
    if instruction is not None and (not isinstance(instruction, str) or not instruction.strip()):
        raise CondaError("Standalone conda executable check returned an invalid instruction.")
    return response
