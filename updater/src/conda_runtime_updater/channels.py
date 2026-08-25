"""Select the package channel used by a new standalone conda runtime."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from importlib.resources import files
from typing import TYPE_CHECKING

from conda.base.constants import CMD_LINE_SOURCE
from conda.base.context import context, reset_context, user_rc_path
from conda.cli.condarc import ConfigurationFile
from conda.exceptions import CondaError

if TYPE_CHECKING:
    from collections.abc import Iterable


CHANNEL_COMMANDS = {
    "create",
    "env_create",
    "env_remove",
    "env_update",
    "install",
    "remove",
    "rename",
    "repoquery",
    "search",
    "update",
}


def registry_choices() -> tuple[tuple[str, str, str], ...]:
    """Return the channel choices in the bundled registry snapshot."""

    path = files("conda_runtime_updater").joinpath("channel-registry.json")
    registry = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        (name, entry["description"], url)
        for name, entry in registry["channels"].items()
        for url in entry["mirrors"]
    )


def should_prompt() -> bool:
    """Return whether this invocation needs interactive channel selection."""

    return (
        not context.channels
        and not context.json
        and not context.quiet
        and not context.dry_run
        and not context.always_yes
        and sys.stdin.isatty()
    )


def prompt_for_channel(choices: Iterable[tuple[str, str, str]]) -> str:
    """Prompt for one channel URL."""

    options = tuple(choices)
    print("Select the package channel for this conda installation:")
    for index, (name, description, url) in enumerate(options, start=1):
        print(f"  {index}. {name}: {description}")
        print(f"     {url}")

    try:
        selected = int(input("Selection: "))
        if selected not in range(1, len(options) + 1):
            raise ValueError
        return options[selected - 1][2]
    except (EOFError, KeyboardInterrupt, ValueError, IndexError) as error:
        raise CondaError("No valid package channel was selected.") from error


def persist_channel(url: str) -> None:
    """Write the selected URL to the user condarc."""

    with ConfigurationFile(user_rc_path, context=context) as configuration:
        configuration.add("channels", url, prepend=True)


def reload_context() -> None:
    """Reload conda configuration without dropping command-line settings."""

    arguments = dict(context.collect_all().get(CMD_LINE_SOURCE, {}))
    if context.prefix_specified:
        arguments["prefix"] = context.target_prefix
    reset_context(argparse_args=Namespace(**arguments))


def select_channel(command: str) -> None:
    """Select and apply a package channel on first interactive use."""

    del command
    if not should_prompt():
        return

    url = prompt_for_channel(registry_choices())
    persist_channel(url)
    reload_context()
