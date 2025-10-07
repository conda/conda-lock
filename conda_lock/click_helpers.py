from collections import OrderedDict
from typing import Any

import click

from click_default_group import DefaultGroup


class OrderedGroup(DefaultGroup):
    def __init__(
        self,
        name: str | None = None,
        commands: dict[str, click.Command] | None = None,
        **kwargs: Any,
    ):
        super().__init__(name, commands, **kwargs)
        #: the registered subcommands by their exported names.
        self.commands = commands or OrderedDict()

    def list_commands(self, ctx: click.Context) -> dict[str, click.Command]:
        return self.commands
