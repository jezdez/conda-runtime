from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from conda.base.constants import UpdateModifier
from conda.exceptions import CondaError
from conda.models.match_spec import MatchSpec

from conda_runtime_updater import helper, plugin
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
        executable=executable,
        lock_path=lock_path,
        ownership="direct",
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
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "display_name": name,
                "install_name": "runtime",
                "metadata_file": path.name,
                "version": "26.5.3",
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
    assert runtime.executable == executable
    assert runtime.ownership == "direct"


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


def test_dry_run_returns_before_discovery(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "context", context_for(tmp_path, dry_run=True))
    monkeypatch.setattr(
        plugin,
        "discover_runtime",
        lambda _prefix: pytest.fail("dry-run discovered runtime metadata"),
    )

    plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())


def test_direct_update_holds_lock_through_apply(monkeypatch, tmp_path, runtime):
    monkeypatch.setattr(plugin, "context", context_for(tmp_path))
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
                "version": "26.5.4",
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
    assert prompted
    assert actions == [("check", None), ("stage", "a" * 64)]

    lock = plugin._session.lock
    plugin.post_command("install")

    assert plugin._session is None
    assert lock.closed
    assert actions[-1] == ("apply", None)


def test_declined_prompt_releases_lock_without_staging(monkeypatch, tmp_path, runtime):
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
    monkeypatch.setattr(
        plugin,
        "confirm_yn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CondaError("declined")),
    )

    with pytest.raises(CondaError, match="declined"):
        plugin.pre_solve(frozenset({MatchSpec("conda")}), frozenset())

    assert plugin._session is None
    assert actions == ["check"]
    lock = plugin.acquire_lock(runtime.lock_path)
    plugin.release_lock(lock)


def test_external_update_reports_instruction_without_staging(monkeypatch, tmp_path, runtime):
    external = RuntimeMetadata(
        prefix=runtime.prefix,
        path=runtime.path,
        executable=runtime.executable,
        lock_path=runtime.lock_path,
        ownership="external",
        instruction="old external instruction",
    )
    monkeypatch.setattr(plugin, "context", context_for(tmp_path))
    monkeypatch.setattr(plugin, "discover_runtime", lambda _prefix: external)
    monkeypatch.setattr(
        plugin,
        "invoke_helper",
        lambda *_args, **_kwargs: {
            "available": True,
            "ownership": "external",
            "instruction": "brew update && brew upgrade conda",
            "version": "26.5.4",
            "build_number": 0,
            "sha256": "a" * 64,
        },
    )

    with pytest.raises(CondaError, match="brew update"):
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


def test_hook_registration_covers_root_solve_commands():
    pre = list(plugin.conda_pre_solves())
    post = list(plugin.conda_post_commands())

    assert len(pre) == 1
    assert pre[0].action is plugin.pre_solve
    assert len(post) == 1
    assert post[0].action is plugin.post_command
    assert post[0].run_for == {"create", "env_update", "install", "update"}
