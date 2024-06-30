from __future__ import annotations

from typing import TYPE_CHECKING

from conda_lock._vendor.cleo.events.console_event import ConsoleEvent


if TYPE_CHECKING:
    from conda_lock._vendor.cleo.commands.command import Command
    from conda_lock._vendor.cleo.io.io import IO


class ConsoleTerminateEvent(ConsoleEvent):
    """
    An event triggered by after the execution of a command.
    """

    def __init__(self, command: Command, io: IO, exit_code: int) -> None:
        super().__init__(command, io)

        self._exit_code = exit_code

    @property
    def exit_code(self) -> int:
        return self._exit_code

    def set_exit_code(self, exit_code: int) -> None:
        self._exit_code = exit_code
