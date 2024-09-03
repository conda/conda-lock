from __future__ import annotations

from typing import TYPE_CHECKING

from conda_lock._vendor.poetry.console.commands.env_command import EnvCommand
from conda_lock._vendor.poetry.console.commands.group_command import GroupCommand


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.installation.installer import Installer


class InstallerCommand(GroupCommand, EnvCommand):
    def __init__(self) -> None:
        # Set in poetry.console.application.Application.configure_installer
        self._installer: Installer | None = None

        super().__init__()

    def reset_poetry(self) -> None:
        super().reset_poetry()

        self.installer.set_package(self.poetry.package)
        self.installer.set_locker(self.poetry.locker)

    @property
    def installer(self) -> Installer:
        assert self._installer is not None
        return self._installer

    def set_installer(self, installer: Installer) -> None:
        self._installer = installer
