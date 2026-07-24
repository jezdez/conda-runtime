from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from conda.base.constants import UpdateModifier
from conda.exceptions import CondaError, CondaSystemExit
from conda.models.match_spec import MatchSpec

from conda_runtime_updater import helper, plugin
from conda_runtime_updater.installation import DetectedInstallation
from conda_runtime_updater.metadata import RuntimeMetadata

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def reset_session():
    plugin._session = None
    yield
    if plugin._session is not None:
        plugin.release_lock(plugin._session.lock)
        plugin._session = None


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeMetadata:
    executable = tmp_path / "conda"
    executable.write_bytes(b"runtime")
    lock_path = tmp_path / ".conda.update.lock"
    lock_path.write_bytes(b"\0")
    return RuntimeMetadata(
        prefix=tmp_path,
        path=tmp_path / ".conda.json",
        version="26.5.3",
        executable=executable,
        lock_path=lock_path,
        ownership="direct",
        installation=None,
        instruction=None,
    )


def context_for(tmp_path: Path, **overrides):
    values = {
        "target_prefix": str(tmp_path),
        "root_prefix": str(tmp_path),
        "update_modifier": UpdateModifier.UPDATE_SPECS,
        "dry_run": False,
        "json": False,
        "quiet": False,
        "offline": False,
        "ignore_pinned": False,
        "pinned_packages": (),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def write_metadata(
    prefix: Path,
    executable: Path,
    *,
    name: str = "conda",
    delegate: str = "conda",
    ownership: str = "direct",
    instruction: str | None = None,
    installation: str | None = None,
    version: str = "26.5.3",
) -> Path:
    path = prefix / f".{name}.json"
    update = {
        "executable": str(executable),
        "ownership": ownership,
        "artifact_name": "conda",
        "channel": "https://conda.anaconda.org/jezdez",
        "package": "conda-runtime",
        "build-number": 0,
        "sha256": "1" * 64,
    }
    if instruction is not None:
        update["instruction"] = instruction
    if installation is not None:
        update["installation"] = installation
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "display_name": name,
                "install_name": "runtime",
                "metadata_file": path.name,
                "version": version,
                "delegate_executable": delegate,
                "channels": [],
                "packages": [],
                "update": update,
            }
        ),
        encoding="utf-8",
    )
    (prefix / f".{name}.update.lock").write_bytes(b"\0")
    return path


def test_trigger_requires_root_conda_or_update_all(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "context", context_for(tmp_path))
    assert plugin.should_coordinate(frozenset({MatchSpec("conda")}))
    assert not plugin.should_coordinate(frozenset({MatchSpec("python")}))

    monkeypatch.setattr(
        plugin,
        "context",
        context_for(tmp_path, update_modifier=UpdateModifier.UPDATE_ALL),
    )
    assert plugin.should_coordinate(frozenset())

    monkeypatch.setattr(
        plugin,
        "context",
        context_for(tmp_path, target_prefix=str(tmp_path / "env")),
    )
    assert not plugin.should_coordinate(frozenset({MatchSpec("conda")}))


def test_discovers_one_valid_runtime_record(tmp_path):
    executable = tmp_path / "conda"
    executable.write_bytes(b"runtime")
    path = write_metadata(tmp_path, executable)

    runtime = plugin.discover_runtime(tmp_path)

    assert runtime is not None
    assert runtime.path == path
    assert runtime.version == "26.5.3"
    assert runtime.executable == executable
    assert runtime.ownership == "direct"
    assert runtime.installation is None


def test_runtime_discovery_fails_closed_on_ambiguity(tmp_path):
    executable = tmp_path / "conda"
    executable.write_bytes(b"runtime")
    write_metadata(tmp_path, executable, name="conda")
    write_metadata(tmp_path, executable, name="other")

    with pytest.raises(CondaError, match="Multiple standalone conda runtime records"):
        plugin.discover_runtime(tmp_path)


def test_runtime_discovery_ignores_non_conda_delegate(tmp_path):
    executable = tmp_path / "tool"
    executable.write_bytes(b"runtime")
    write_metadata(tmp_path, executable, name="tool", delegate="tool")

    assert plugin.discover_runtime(tmp_path) is None


def test_runtime_discovery_rejects_invalid_version(tmp_path):
    executable = tmp_path / "conda"
    executable.write_bytes(b"runtime")
    write_metadata(tmp_path, executable, version="26.5")

    with pytest.raises(CondaError, match="runtime version is invalid"):
        plugin.discover_runtime(tmp_path)


def test_runtime_discovery_validates_installation_identifier(tmp_path):
    executable = tmp_path / "conda"
    executable.write_bytes(b"runtime")
    write_metadata(tmp_path, executable, installation="Homebrew")

    with pytest.raises(CondaError, match="installation is invalid"):
        plugin.discover_runtime(tmp_path)


def test_runtime_pin_uses_conda_context(monkeypatch):
    monkeypatch.setattr(
        plugin.context,
        "pinned_packages",
        ("python 3.12.*", "conda >=26"),
    )

    plugin.pin_runtime_conda("26.5.4.post1")

    assert plugin.context.pinned_packages == ("python 3.12.*", "conda ==26.5.4")


def test_dry_run_pins_current_runtime_without_helper_or_lock(monkeypatch, tmp_path, runtime):
    conda_context = context_for(
        tmp_path,
        dry_run=True,
        pinned_packages=("python 3.12.*", "conda >=26"),
    )
    monkeypatch.setattr(plugin, "context", conda_context)
    monkeypatch.setattr(plugin, "discover_runtime", lambda _prefix: runtime)
    monkeypatch.setattr(
        plugin,
        "invoke_helper",
        lambda *_args, **_kwargs: pytest.fail("dry-run invoked the executable helper"),
    )
    monkeypatch.setattr(
        plugin,
        "acquire_lock",
        lambda *_args, **_kwargs: pytest.fail("dry-run acquired the runtime lock"),
    )

    plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())

    assert conda_context.pinned_packages == ("python 3.12.*", "conda ==26.5.3")


def test_no_pin_fails_before_helper_or_lock(monkeypatch, tmp_path, runtime):
    monkeypatch.setattr(plugin, "context", context_for(tmp_path, ignore_pinned=True))
    monkeypatch.setattr(plugin, "discover_runtime", lambda _prefix: runtime)
    monkeypatch.setattr(
        plugin,
        "invoke_helper",
        lambda *_args, **_kwargs: pytest.fail("--no-pin invoked the executable helper"),
    )
    monkeypatch.setattr(
        plugin,
        "acquire_lock",
        lambda *_args, **_kwargs: pytest.fail("--no-pin acquired the runtime lock"),
    )

    with pytest.raises(CondaError, match="cannot be coordinated with --no-pin"):
        plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())


def test_no_outer_update_pins_current_runtime_version(monkeypatch, tmp_path, runtime):
    conda_context = context_for(
        tmp_path,
        pinned_packages=("python 3.12.*", "conda <99"),
    )
    monkeypatch.setattr(plugin, "context", conda_context)
    monkeypatch.setattr(plugin, "discover_runtime", lambda _prefix: runtime)
    monkeypatch.setattr(
        plugin,
        "invoke_helper",
        lambda *_args, **_kwargs: {
            "available": False,
            "ownership": "direct",
        },
    )

    plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())

    assert conda_context.pinned_packages == ("python 3.12.*", "conda ==26.5.3")
    lock = plugin.acquire_lock(runtime.lock_path)
    plugin.release_lock(lock)


def test_detected_installer_records_external_ownership_before_check(monkeypatch, tmp_path):
    executable = tmp_path / "conda"
    executable.write_bytes(b"runtime")
    metadata_path = write_metadata(tmp_path, executable)
    instruction = (
        "This conda runtime is managed by pip. Run owner-python -m pip install --upgrade "
        "conda-runtime, then retry conda self update."
    )
    monkeypatch.setattr(plugin, "context", context_for(tmp_path))
    monkeypatch.setattr(
        plugin,
        "detect_external_installation",
        lambda _runtime: DetectedInstallation(
            name="pip",
            executable=executable,
            instruction=instruction,
        ),
    )
    actions = []
    acquire_lock = plugin.acquire_lock

    def acquire(path):
        actions.append(("acquire-lock", {}))
        return acquire_lock(path)

    monkeypatch.setattr(plugin, "acquire_lock", acquire)

    def invoke(runtime, action, **kwargs):
        actions.append((action, kwargs))
        if action == "record-installation":
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            data["update"]["ownership"] = "external"
            data["update"]["installation"] = "pip"
            data["update"]["executable"] = str(executable)
            data["update"]["instruction"] = instruction
            metadata_path.write_text(json.dumps(data), encoding="utf-8")
            return {
                "recorded": True,
                "ownership": "external",
                "installation": "pip",
                "executable": str(executable),
                "instruction": instruction,
            }
        assert runtime.ownership == "external"
        assert runtime.installation == "pip"
        assert runtime.instruction == instruction
        return {
            "available": True,
            "ownership": "external",
            "installation": "pip",
            "instruction": instruction,
            "version": "26.5.4",
            "build_number": 0,
            "sha256": "a" * 64,
        }

    monkeypatch.setattr(plugin, "invoke_helper", invoke)

    with pytest.raises(CondaError, match="owner-python"):
        plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())

    assert actions == [
        (
            "record-installation",
            {
                "ownership": "external",
                "installation": "pip",
                "executable": executable,
                "instruction": instruction,
            },
        ),
        ("acquire-lock", {}),
        ("check", {}),
    ]
    assert plugin._session is None
    recorded = plugin.discover_runtime(tmp_path)
    assert recorded is not None
    assert recorded.ownership == "external"
    assert recorded.installation == "pip"
    assert recorded.instruction == instruction


def test_invalid_candidate_version_fails_before_prompt_or_stage(monkeypatch, tmp_path, runtime):
    monkeypatch.setattr(plugin, "context", context_for(tmp_path))
    monkeypatch.setattr(plugin, "discover_runtime", lambda _prefix: runtime)
    monkeypatch.setattr(
        plugin,
        "confirm_yn",
        lambda *_args, **_kwargs: pytest.fail("invalid candidate prompted"),
    )
    actions = []

    def invoke(_runtime, action, *, candidate=None):
        actions.append((action, candidate))
        return {
            "available": True,
            "ownership": "direct",
            "instruction": None,
            "version": "26.5",
            "build_number": 0,
            "sha256": "a" * 64,
        }

    monkeypatch.setattr(plugin, "invoke_helper", invoke)

    with pytest.raises(CondaError, match="runtime version is invalid"):
        plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())

    assert actions == [("check", None)]
    lock = plugin.acquire_lock(runtime.lock_path)
    plugin.release_lock(lock)


def test_direct_update_holds_lock_through_apply(monkeypatch, tmp_path, runtime):
    conda_context = context_for(
        tmp_path,
        pinned_packages=("python 3.12.*", "conda >=26"),
    )
    monkeypatch.setattr(plugin, "context", conda_context)
    monkeypatch.setattr(plugin, "discover_runtime", lambda _prefix: runtime)
    prompted = []
    monkeypatch.setattr(plugin, "confirm_yn", lambda message, default: prompted.append(message))
    actions = []

    def invoke(_runtime, action, *, candidate=None):
        actions.append((action, candidate))
        if action == "check":
            return {
                "available": True,
                "ownership": "direct",
                "instruction": None,
                "version": "26.5.4.post1",
                "build_number": 0,
                "sha256": "a" * 64,
            }
        if action == "stage":
            return {"staged": True}
        return {"applied": True}

    monkeypatch.setattr(plugin, "invoke_helper", invoke)

    plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())

    assert plugin._session is not None
    assert not plugin._session.lock.closed
    assert conda_context.pinned_packages == ("python 3.12.*", "conda ==26.5.4")
    assert prompted
    assert actions == [("check", None), ("stage", "a" * 64)]

    lock = plugin._session.lock
    plugin.post_command("install")

    assert plugin._session is None
    assert lock.closed
    assert actions[-1] == ("apply", None)


def test_declined_prompt_releases_lock_without_retrying(monkeypatch, tmp_path, runtime):
    monkeypatch.setattr(plugin, "context", context_for(tmp_path))
    monkeypatch.setattr(plugin, "discover_runtime", lambda _prefix: runtime)
    actions = []

    def invoke(_runtime, action, *, candidate=None):
        actions.append(action)
        return {
            "available": True,
            "ownership": "direct",
            "instruction": None,
            "version": "26.5.4",
            "build_number": 0,
            "sha256": "a" * 64,
        }

    monkeypatch.setattr(plugin, "invoke_helper", invoke)
    prompts = []

    def decline(*_args, **_kwargs):
        prompts.append(True)
        raise CondaSystemExit("Exiting.")

    monkeypatch.setattr(plugin, "confirm_yn", decline)

    with pytest.raises(CondaSystemExit, match="Exiting") as error:
        plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())

    assert plugin._session is None
    assert actions == ["check"]
    assert prompts == [True]
    assert error.value.allow_retry is False
    lock = plugin.acquire_lock(runtime.lock_path)
    plugin.release_lock(lock)


def test_external_update_reports_instruction_without_staging(monkeypatch, tmp_path, runtime):
    external = RuntimeMetadata(
        prefix=runtime.prefix,
        path=runtime.path,
        version=runtime.version,
        executable=runtime.executable,
        lock_path=runtime.lock_path,
        ownership="external",
        installation="homebrew",
        instruction=None,
    )
    monkeypatch.setattr(plugin, "context", context_for(tmp_path))
    monkeypatch.setattr(plugin, "discover_runtime", lambda _prefix: external)
    monkeypatch.setattr(
        plugin,
        "detect_external_installation",
        lambda _runtime: pytest.fail("classified external runtime was detected again"),
    )
    monkeypatch.setattr(
        plugin,
        "invoke_helper",
        lambda *_args, **_kwargs: {
            "available": True,
            "ownership": "external",
            "installation": "homebrew",
            "instruction": None,
            "version": "26.5.4",
            "build_number": 0,
            "sha256": "a" * 64,
        },
    )

    with pytest.raises(
        CondaError,
        match=r"brew update && brew upgrade conda, then retry conda self update",
    ):
        plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())

    assert plugin._session is None


def test_json_mode_does_not_prompt_and_defers_apply_failure(
    monkeypatch, capsys, tmp_path, runtime
):
    monkeypatch.setattr(plugin, "context", context_for(tmp_path, json=True))
    monkeypatch.setattr(plugin, "discover_runtime", lambda _prefix: runtime)
    monkeypatch.setattr(
        plugin,
        "confirm_yn",
        lambda *_args, **_kwargs: pytest.fail("JSON mode prompted"),
    )

    def invoke(_runtime, action, *, candidate=None):
        if action == "check":
            return {
                "available": True,
                "ownership": "direct",
                "instruction": None,
                "version": "26.5.4",
                "build_number": 0,
                "sha256": "a" * 64,
            }
        if action == "stage":
            return {"staged": True}
        raise CondaError("apply failed")

    monkeypatch.setattr(plugin, "invoke_helper", invoke)

    plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())
    plugin.post_command("install")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "requires recovery" in captured.err
    assert plugin._session is None


def test_json_mode_defers_lock_release_failure(monkeypatch, capsys, tmp_path, runtime):
    monkeypatch.setattr(plugin, "context", context_for(tmp_path, json=True))
    lock = plugin.acquire_lock(runtime.lock_path)
    plugin._session = plugin.UpdateSession(runtime=runtime, lock=lock)
    monkeypatch.setattr(plugin, "invoke_helper", lambda *_args, **_kwargs: {"applied": True})

    def fail_release(handle):
        handle.close()
        raise OSError("unlock failed")

    monkeypatch.setattr(plugin, "release_lock", fail_release)

    plugin.post_command("install")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unlock failed" in captured.err
    assert plugin._session is None


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (subprocess.TimeoutExpired(["conda"], 600), "timed out"),
        (OSError("cannot execute"), "Could not start"),
    ],
)
def test_helper_process_failures_are_conda_errors(monkeypatch, runtime, error, message):
    monkeypatch.setattr(helper, "context", SimpleNamespace(offline=False))
    monkeypatch.setattr(
        helper.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(CondaError, match=message):
        helper.invoke_helper(runtime, "check")


def test_record_installation_helper_sets_only_requested_fields(monkeypatch, runtime):
    monkeypatch.setattr(helper, "context", SimpleNamespace(offline=False))
    captured = {}

    def run(_arguments, **kwargs):
        captured.update(kwargs["env"])
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "recorded": True,
                    "ownership": "external",
                    "installation": "homebrew",
                    "executable": str(runtime.executable),
                    "instruction": None,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(helper.subprocess, "run", run)

    response = helper.invoke_helper(
        runtime,
        "record-installation",
        ownership="external",
        installation="homebrew",
        executable=runtime.executable,
    )
    helper.validate_record_installation(
        response,
        ownership="external",
        installation="homebrew",
        executable=runtime.executable,
        instruction=None,
    )

    assert captured[helper.ACTION_ENV] == "v1/record-installation"
    assert captured[helper.OWNERSHIP_ENV] == "external"
    assert captured[helper.INSTALLATION_ENV] == "homebrew"
    assert captured[helper.EXECUTABLE_ENV] == str(runtime.executable)
    assert helper.INSTRUCTION_ENV not in captured


def test_hook_registration_covers_root_solve_commands():
    pre = list(plugin.conda_pre_solves())
    post = list(plugin.conda_post_commands())

    assert len(pre) == 1
    assert pre[0].action is plugin.pre_solve
    assert len(post) == 1
    assert post[0].action is plugin.post_command
    assert post[0].run_for == {"create", "env_update", "install", "update"}
