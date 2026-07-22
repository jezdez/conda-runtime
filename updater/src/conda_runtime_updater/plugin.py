"""Coordinate stamped executable updates with root-prefix conda transactions."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from conda import plugins
from conda.base.constants import UpdateModifier
from conda.base.context import context
from conda.common.path import paths_equal
from conda.exceptions import CondaError
from conda.plugins.types import CondaPostCommand, CondaPreSolve
from conda.reporters import confirm_yn

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import Any, BinaryIO

    from conda.models.match_spec import MatchSpec

ACTION_ENV = "CONDA_SHIP_INTERNAL_UPDATE"
CANDIDATE_ENV = "CONDA_SHIP_INTERNAL_UPDATE_CANDIDATE"
OFFLINE_ENV = "CONDA_SHIP_INTERNAL_UPDATE_OFFLINE"
PREFIX_ENV = "CONDA_SHIP_PREFIX"


@dataclass(frozen=True)
class RuntimeMetadata:
    prefix: Path
    path: Path
    executable: Path
    lock_path: Path
    ownership: str
    instruction: str | None


@dataclass
class UpdateSession:
    runtime: RuntimeMetadata
    lock: BinaryIO


_session: UpdateSession | None = None


def should_coordinate(specs_to_add: frozenset[MatchSpec]) -> bool:
    """Return whether this solve can update the managed root conda package."""

    return paths_equal(context.target_prefix, context.root_prefix) and (
        context.update_modifier == UpdateModifier.UPDATE_ALL
        or any(spec.name == "conda" for spec in specs_to_add)
    )


def pre_solve(
    specs_to_add: frozenset[MatchSpec],
    specs_to_remove: frozenset[MatchSpec],
) -> None:
    """Check and stage an outer update before a matching root solve."""

    del specs_to_remove
    global _session

    if _session is not None or context.dry_run or not should_coordinate(specs_to_add):
        return

    runtime = discover_runtime(Path(context.root_prefix))
    if runtime is None:
        return

    lock = acquire_lock(runtime.lock_path)
    try:
        result = invoke_helper(runtime, "check")
        check = validate_check(result, runtime)
        if not check["available"]:
            return

        if runtime.ownership == "external":
            instruction = check["instruction"] or runtime.instruction or (
                "Update the conda executable with the package manager that installed it, "
                "then retry."
            )
            raise CondaError(instruction)

        version = check["version"]
        if not context.json:
            confirm_yn(
                f"Update the standalone conda runtime to {version} together with its managed "
                "conda installation?",
                default="yes",
            )

        staged = invoke_helper(runtime, "stage", candidate=check["sha256"])
        if staged.get("staged") is not True:
            raise CondaError("The standalone conda executable update was not staged.")

        _session = UpdateSession(runtime=runtime, lock=lock)
        lock = None
    finally:
        if lock is not None:
            release_lock(lock)


def post_command(command: str) -> None:
    """Apply a staged outer update after the inner command succeeds."""

    del command
    global _session

    if _session is None:
        return

    session = _session
    _session = None
    failure: Exception | None = None
    try:
        applied = invoke_helper(session.runtime, "apply")
        if applied.get("applied") is not True and applied.get("replacement_pending") is not True:
            raise CondaError("The standalone conda executable update was not applied.")
    except Exception as error:
        failure = error
    try:
        release_lock(session.lock)
    except Exception as error:
        if failure is None:
            failure = error

    if failure is None:
        return
    if not context.json:
        raise failure
    if not context.quiet:
        print(
            "The conda package update succeeded, but the executable update requires "
            f"recovery: {failure}",
            file=sys.stderr,
        )


def discover_runtime(prefix: Path) -> RuntimeMetadata | None:
    """Find one update-enabled conda-ship ownership record in a root prefix."""

    matches = []
    for path in sorted(prefix.glob(".*.json")):
        candidate = read_runtime_metadata(prefix, path)
        if candidate is not None:
            matches.append(candidate)

    if len(matches) > 1:
        paths = ", ".join(str(runtime.path) for runtime in matches)
        raise CondaError(f"Multiple standalone conda runtime records were found: {paths}")
    return matches[0] if matches else None


def read_runtime_metadata(prefix: Path, path: Path) -> RuntimeMetadata | None:
    """Read one recognized runtime record and reject an invalid update identity."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise CondaError(f"Runtime metadata is not a regular file: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CondaError(f"Could not read runtime metadata at {path}: {error}") from error
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != 1 or data.get("metadata_file") != path.name:
        return None
    if data.get("delegate_executable") != "conda":
        return None

    update = data.get("update")
    if update is None:
        return None
    if not isinstance(update, dict):
        raise CondaError(f"Runtime update metadata is invalid: {path}")

    ownership = require_string(update, "ownership", path)
    if ownership not in {"direct", "external"}:
        raise CondaError(f"Runtime update ownership is invalid: {path}")
    for key in ("artifact_name", "channel", "package", "sha256"):
        require_string(update, key, path)
    build_number = update.get("build-number")
    if not isinstance(build_number, int) or isinstance(build_number, bool) or build_number < 0:
        raise CondaError(f"Runtime update build number is invalid: {path}")

    instruction = update.get("instruction")
    if instruction is not None and (
        not isinstance(instruction, str) or not instruction.strip()
    ):
        raise CondaError(f"Runtime update instruction is invalid: {path}")
    if ownership == "direct" and instruction is not None:
        raise CondaError(f"A directly managed runtime cannot have an external instruction: {path}")

    executable = Path(require_string(update, "executable", path))
    if not executable.is_absolute() or not executable.is_file():
        raise CondaError(
            f"Runtime executable is missing or is not an absolute file path: {executable}"
        )

    stem = path.name.removesuffix(".json")
    lock_path = prefix / f"{stem}.update.lock"
    try:
        lock_metadata = lock_path.lstat()
    except FileNotFoundError as error:
        raise CondaError(f"Runtime update coordination lock is missing: {lock_path}") from error
    if lock_path.is_symlink() or not stat.S_ISREG(lock_metadata.st_mode):
        raise CondaError(f"Runtime update coordination lock is not a regular file: {lock_path}")
    if lock_metadata.st_size < 1:
        raise CondaError(f"Runtime update coordination lock is not initialized: {lock_path}")

    return RuntimeMetadata(
        prefix=prefix,
        path=path,
        executable=executable,
        lock_path=lock_path,
        ownership=ownership,
        instruction=instruction,
    )


def require_string(data: Mapping[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise CondaError(f"Runtime update field {key!r} is invalid: {path}")
    return value


def acquire_lock(path: Path) -> BinaryIO:
    """Open and exclusively lock the runtime's pre-created one-byte lock file."""

    lock = path.open("r+b", buffering=0)
    try:
        if sys.platform == "win32":
            import msvcrt

            while True:
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return lock
    except BaseException:
        lock.close()
        raise


def release_lock(lock: BinaryIO) -> None:
    """Unlock and close a coordinator lock handle."""

    try:
        lock.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    finally:
        lock.close()


def invoke_helper(
    runtime: RuntimeMetadata,
    action: str,
    *,
    candidate: str | None = None,
) -> dict[str, Any]:
    """Invoke one version-one action on the stamped outer executable."""

    env = os.environ.copy()
    env[PREFIX_ENV] = str(runtime.prefix)
    env[ACTION_ENV] = f"v1/{action}"
    env.pop(CANDIDATE_ENV, None)
    env.pop(OFFLINE_ENV, None)
    if candidate is not None:
        env[CANDIDATE_ENV] = candidate
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


def validate_check(
    response: dict[str, Any],
    runtime: RuntimeMetadata,
) -> dict[str, Any]:
    """Validate the check fields used by the coordinator."""

    if not isinstance(response.get("available"), bool):
        raise CondaError("Standalone conda executable check omitted update availability.")
    if response.get("ownership") != runtime.ownership:
        raise CondaError("Standalone conda executable ownership changed during the update check.")
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
    if instruction is not None and (
        not isinstance(instruction, str) or not instruction.strip()
    ):
        raise CondaError("Standalone conda executable check returned an invalid instruction.")
    return response


@plugins.hookimpl
def conda_pre_solves() -> Iterable[CondaPreSolve]:
    yield CondaPreSolve(name="conda-runtime-update", action=pre_solve)


@plugins.hookimpl
def conda_post_commands() -> Iterable[CondaPostCommand]:
    yield CondaPostCommand(
        name="conda-runtime-update",
        action=post_command,
        run_for={"create", "env_update", "install", "update"},
    )
