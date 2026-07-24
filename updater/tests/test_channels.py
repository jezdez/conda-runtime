from __future__ import annotations

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
    monkeypatch.setattr(channels, "context", context)
    monkeypatch.setattr(channels, "user_rc_path", user_condarc)
    monkeypatch.setattr(channels.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    channels.select_channel("create")

    assert context.channels == ("https://prefix.dev/conda-forge",)
    assert user_condarc.read_text(encoding="utf-8") == (
        "channels:\n  - https://prefix.dev/conda-forge\n"
    )
    output = capsys.readouterr().out
    assert "conda-forge: Community-maintained conda package channel." in output
    assert "https://prefix.dev/conda-forge" in output


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
