from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from argparse import Namespace
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from conda.base.context import Context

from conda_runtime_updater import channels

if TYPE_CHECKING:
    from pathlib import Path


def conda_context(tmp_path: Path, **overrides) -> Context:
    runtime_condarc = tmp_path / "runtime.yaml"
    runtime_condarc.write_text("channel_priority: strict\n", encoding="utf-8")
    arguments = {
        "channel": (),
        "override_channels": False,
        "json": False,
        "quiet": False,
        "dry_run": False,
        "yes": False,
    }
    arguments.update(overrides)
    return Context(
        search_path=(runtime_condarc, tmp_path / "user.yaml"),
        argparse_args=Namespace(**arguments),
    )


def test_selects_persists_and_applies_channel(monkeypatch, capsys, tmp_path):
    context = conda_context(tmp_path)
    user_condarc = tmp_path / "user.yaml"
    reloaded = []
    monkeypatch.setattr(channels, "context", context)
    monkeypatch.setattr(channels, "user_rc_path", user_condarc)
    monkeypatch.setattr(
        channels,
        "reset_context",
        lambda *, argparse_args: reloaded.append(
            Context(
                search_path=(tmp_path / "runtime.yaml", user_condarc),
                argparse_args=argparse_args,
            )
        ),
    )
    monkeypatch.setattr(channels.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _prompt: "3")

    channels.select_channel("create")

    assert reloaded[0].channels == ("https://prefix.dev/conda-forge",)
    assert user_condarc.read_text(encoding="utf-8") == (
        "channels:\n  - https://prefix.dev/conda-forge\n"
    )
    output = capsys.readouterr().out
    assert "conda-forge: Community-maintained conda package channel." in output
    assert "https://prefix.dev/conda-forge" in output


def test_anaconda_main_uses_the_tos_channel_url():
    assert (
        "anaconda-main",
        "Anaconda-maintained packages from the main channel.",
        "https://repo.anaconda.com/pkgs/main",
    ) in channels.registry_choices()


def test_reload_preserves_command_line_configuration(monkeypatch, tmp_path):
    target_prefix = tmp_path / "env"
    context = conda_context(
        tmp_path,
        prefix=str(target_prefix),
        offline=True,
        solver="classic",
        subdir="linux-64",
    )
    arguments = []
    monkeypatch.setattr(channels, "context", context)
    monkeypatch.setattr(
        channels,
        "reset_context",
        lambda *, argparse_args: arguments.append(argparse_args),
    )

    channels.reload_context()

    assert vars(arguments[0])["prefix"] == str(target_prefix)
    assert vars(arguments[0])["offline"] is True
    assert vars(arguments[0])["solver"] == "classic"
    assert vars(arguments[0])["subdir"] == "linux-64"


def test_channel_commands_cover_anaconda_tos_commands():
    assert {
        "create",
        "env_create",
        "env_remove",
        "env_update",
        "install",
        "remove",
        "rename",
        "search",
        "update",
    } <= channels.CHANNEL_COMMANDS


def test_first_command_uses_persisted_channel_offline(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    user_condarc = home / ".condarc"
    environment = os.environ.copy()
    environment.update(
        {
            "CONDARC": str(user_condarc),
            "CONDA_ENVS_PATH": str(tmp_path / "envs"),
            "CONDA_PKGS_DIRS": str(tmp_path / "pkgs"),
            "HOME": str(home),
            "PYTHONNOUSERSITE": "1",
            "USERPROFILE": str(home),
        }
    )
    environment.pop("CONDA_CHANNELS", None)
    command = [
        sys.executable,
        "-c",
        textwrap.dedent(
            """
            import builtins
            import os
            import sys

            from conda.base.context import context
            from conda.cli.main import main
            from conda_runtime_updater import plugin

            if not context.plugin_manager.is_registered(plugin):
                context.plugin_manager.register(plugin)

            class Input:
                def isatty(self):
                    return True

            def fail_if_prompted(_prompt=""):
                raise RuntimeError("channel selection prompted again")

            sys.stdin = Input()
            selection = os.environ.get("CHANNEL_SELECTION")
            builtins.input = (
                (lambda _prompt="": selection) if selection is not None else fail_if_prompted
            )
            raise SystemExit(
                main(
                    "search",
                    "package-that-does-not-exist-conda-runtime-proof",
                    "--offline",
                )
            )
            """
        ),
    ]

    first_environment = environment | {"CHANNEL_SELECTION": "1"}
    first = subprocess.run(
        command,
        check=False,
        capture_output=True,
        env=first_environment,
        text=True,
    )

    assert first.returncode == 1, first.stderr
    assert "Select the package channel" in first.stdout
    assert "https://repo.anaconda.com/pkgs/main/" in first.stdout + first.stderr
    assert user_condarc.read_text(encoding="utf-8") == (
        "channels:\n  - https://repo.anaconda.com/pkgs/main\n"
    )

    second = subprocess.run(
        command,
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert second.returncode == 1, second.stderr
    assert "Select the package channel" not in second.stdout
    assert "channel selection prompted again" not in second.stderr


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"json": True}, id="json"),
        pytest.param({"quiet": True}, id="quiet"),
        pytest.param({"dry_run": True}, id="dry-run"),
        pytest.param({"yes": True}, id="always-yes"),
        pytest.param(
            {"channel": ("https://conda.anaconda.org/conda-forge",)},
            id="explicit-channel",
        ),
    ],
)
def test_skips_noninteractive_modes_and_explicit_channels(monkeypatch, tmp_path, overrides):
    context = conda_context(tmp_path, **overrides)
    user_condarc = tmp_path / "user.yaml"
    monkeypatch.setattr(channels, "context", context)
    monkeypatch.setattr(channels, "user_rc_path", user_condarc)
    monkeypatch.setattr(channels.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: pytest.fail("channel selection prompted"),
    )

    channels.select_channel("create")

    assert not user_condarc.exists()


def test_skips_without_a_terminal(monkeypatch, tmp_path):
    context = conda_context(tmp_path)
    user_condarc = tmp_path / "user.yaml"
    monkeypatch.setattr(channels, "context", context)
    monkeypatch.setattr(channels, "user_rc_path", user_condarc)
    monkeypatch.setattr(channels.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: pytest.fail("channel selection prompted"),
    )

    channels.select_channel("create")

    assert not user_condarc.exists()


def test_skips_existing_user_channel(monkeypatch, tmp_path):
    user_condarc = tmp_path / "user.yaml"
    user_condarc.write_text(
        "channels:\n  - https://prefix.dev/conda-forge\n",
        encoding="utf-8",
    )
    context = conda_context(tmp_path)
    monkeypatch.setattr(channels, "context", context)
    monkeypatch.setattr(channels, "user_rc_path", user_condarc)
    monkeypatch.setattr(channels.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: pytest.fail("channel selection prompted"),
    )

    channels.select_channel("create")

    assert context.channels == ("https://prefix.dev/conda-forge",)
