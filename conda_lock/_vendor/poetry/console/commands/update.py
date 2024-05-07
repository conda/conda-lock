from __future__ import annotations

from conda_lock._vendor.cleo.helpers import argument
from conda_lock._vendor.cleo.helpers import option

from conda_lock._vendor.poetry.console.commands.installer_command import InstallerCommand


class UpdateCommand(InstallerCommand):
    name = "update"
    description = (
        "Update the dependencies as according to the <comment>pyproject.toml</> file."
    )

    arguments = [
        argument("packages", "The packages to update", optional=True, multiple=True)
    ]
    options = [
        *InstallerCommand._group_dependency_options(),
        option(
            "no-dev",
            None,
            "Do not update the development dependencies."
            " (<warning>Deprecated</warning>)",
        ),
        option(
            "sync",
            None,
            "Synchronize the environment with the locked packages and the specified"
            " groups.",
        ),
        option(
            "dry-run",
            None,
            "Output the operations but do not execute anything "
            "(implicitly enables --verbose).",
        ),
        option("lock", None, "Do not perform operations (only update the lockfile)."),
    ]

    loggers = ["poetry.repositories.pypi_repository"]

    def handle(self) -> int:
        packages = self.argument("packages")
        if packages:
            self.installer.whitelist({name: "*" for name in packages})

        self.installer.only_groups(self.activated_groups)
        self.installer.dry_run(self.option("dry-run"))
        self.installer.requires_synchronization(self.option("sync"))
        self.installer.execute_operations(not self.option("lock"))

        # Force update
        self.installer.update(True)

        return self.installer.run()
