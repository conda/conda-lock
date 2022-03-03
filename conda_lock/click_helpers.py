from collections import OrderedDict
from typing import Any, Mapping, Optional

import click

from click_default_group import DefaultGroup


class OrderedGroup(DefaultGroup):
    def __init__(
        self,
        name: Optional[str] = None,
        commands: Optional[Mapping[str, click.Command]] = None,
        **kwargs: Any
    ):
        super(OrderedGroup, self).__init__(name, commands, **kwargs)
        #: the registered subcommands by their exported names.
        self.commands = commands or OrderedDict()

    def list_commands(self, ctx: click.Context) -> Mapping[str, click.Command]:
        return self.commands
