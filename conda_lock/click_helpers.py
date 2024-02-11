from collections import OrderedDict
from typing import Any, Dict, Optional

import click

from click_default_group import DefaultGroup


class OrderedGroup(DefaultGroup):
    def __init__(
        self,
        name: Optional[str] = None,
        commands: Optional[Dict[str, click.Command]] = None,
        **kwargs: Any,
    ):
        super().__init__(name, commands, **kwargs)
        #: the registered subcommands by their exported names.
        self.commands = commands or OrderedDict()

    def list_commands(self, ctx: click.Context) -> Dict[str, click.Command]:
        return self.commands
