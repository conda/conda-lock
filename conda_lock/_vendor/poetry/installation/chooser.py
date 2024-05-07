from __future__ import annotations

import logging
import re

from typing import TYPE_CHECKING
from typing import Any

from conda_lock._vendor.poetry.config.config import Config
from conda_lock._vendor.poetry.config.config import PackageFilterPolicy
from conda_lock._vendor.poetry.repositories.http_repository import HTTPRepository
from conda_lock._vendor.poetry.utils.helpers import get_highest_priority_hash_type
from conda_lock._vendor.poetry.utils.wheel import Wheel


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.constraints.version import Version
    from conda_lock._vendor.poetry.core.packages.package import Package
    from conda_lock._vendor.poetry.core.packages.utils.link import Link

    from conda_lock._vendor.poetry.repositories.repository_pool import RepositoryPool
    from conda_lock._vendor.poetry.utils.env import Env


logger = logging.getLogger(__name__)


class Chooser:
    """
    A Chooser chooses an appropriate release archive for packages.
    """

    def __init__(
        self, pool: RepositoryPool, env: Env, config: Config | None = None
    ) -> None:
        self._pool = pool
        self._env = env
        self._config = config or Config.create()
        self._no_binary_policy: PackageFilterPolicy = PackageFilterPolicy(
            self._config.get("installer.no-binary", [])
        )

    def choose_for(self, package: Package) -> Link:
        """
        Return the url of the selected archive for a given package.
        """
        links = []
        for link in self._get_links(package):
            if link.is_wheel:
                if not self._no_binary_policy.allows(package.name):
                    logger.debug(
                        "Skipping wheel for %s as requested in no binary policy for"
                        " package (%s)",
                        link.filename,
                        package.name,
                    )
                    continue

                if not Wheel(link.filename).is_supported_by_environment(self._env):
                    logger.debug(
                        "Skipping wheel %s as this is not supported by the current"
                        " environment",
                        link.filename,
                    )
                    continue

            if link.ext in {".egg", ".exe", ".msi", ".rpm", ".srpm"}:
                logger.debug("Skipping unsupported distribution %s", link.filename)
                continue

            links.append(link)

        if not links:
            raise RuntimeError(f"Unable to find installation candidates for {package}")

        # Get the best link
        chosen = max(links, key=lambda link: self._sort_key(package, link))

        return chosen

    def _get_links(self, package: Package) -> list[Link]:
        if package.source_type:
            assert package.source_reference is not None
            repository = self._pool.repository(package.source_reference)

        elif not self._pool.has_repository("pypi"):
            repository = self._pool.repositories[0]
        else:
            repository = self._pool.repository("pypi")
        links = repository.find_links_for_package(package)

        locked_hashes = {f["hash"] for f in package.files}
        if not locked_hashes:
            return links

        selected_links = []
        skipped = []
        locked_hash_names = {h.split(":")[0] for h in locked_hashes}
        for link in links:
            if not link.hashes:
                selected_links.append(link)
                continue

            link_hash: str | None = None
            if (candidates := locked_hash_names.intersection(link.hashes.keys())) and (
                hash_name := get_highest_priority_hash_type(candidates, link.filename)
            ):
                link_hash = f"{hash_name}:{link.hashes[hash_name]}"

            elif isinstance(repository, HTTPRepository):
                link_hash = repository.calculate_sha256(link)

            if link_hash not in locked_hashes:
                skipped.append((link.filename, link_hash))
                logger.debug(
                    "Skipping %s as %s checksum does not match expected value",
                    link.filename,
                    link_hash,
                )
                continue

            selected_links.append(link)

        if links and not selected_links:
            links_str = ", ".join(f"{link}({h})" for link, h in skipped)
            raise RuntimeError(
                f"Retrieved digests for links {links_str} not in poetry.lock"
                f" metadata {locked_hashes}"
            )

        return selected_links

    def _sort_key(
        self, package: Package, link: Link
    ) -> tuple[int, int, int, Version, tuple[Any, ...], int]:
        """
        Function to pass as the `key` argument to a call to sorted() to sort
        InstallationCandidates by preference.
        Returns a tuple such that tuples sorting as greater using Python's
        default comparison operator are more preferred.
        The preference is as follows:
        First and foremost, candidates with allowed (matching) hashes are
        always preferred over candidates without matching hashes. This is
        because e.g. if the only candidate with an allowed hash is yanked,
        we still want to use that candidate.
        Second, excepting hash considerations, candidates that have been
        yanked (in the sense of PEP 592) are always less preferred than
        candidates that haven't been yanked. Then:
        If not finding wheels, they are sorted by version only.
        If finding wheels, then the sort order is by version, then:
          1. existing installs
          2. wheels ordered via Wheel.support_index_min(self._supported_tags)
          3. source archives
        If prefer_binary was set, then all wheels are sorted above sources.
        Note: it was considered to embed this logic into the Link
              comparison operators, but then different sdist links
              with the same version, would have to be considered equal
        """
        build_tag: tuple[Any, ...] = ()
        binary_preference = 0
        if link.is_wheel:
            wheel = Wheel(link.filename)
            if not wheel.is_supported_by_environment(self._env):
                raise RuntimeError(
                    f"{wheel.filename} is not a supported wheel for this platform. It "
                    "can't be sorted."
                )

            # TODO: Binary preference
            pri = -(wheel.get_minimum_supported_index(self._env.supported_tags) or 0)
            if wheel.build_tag is not None:
                match = re.match(r"^(\d+)(.*)$", wheel.build_tag)
                if not match:
                    raise ValueError(f"Unable to parse build tag: {wheel.build_tag}")
                build_tag_groups = match.groups()
                build_tag = (int(build_tag_groups[0]), build_tag_groups[1])
        else:  # sdist
            support_num = len(self._env.supported_tags)
            pri = -support_num

        has_allowed_hash = int(self._is_link_hash_allowed_for_package(link, package))

        yank_value = int(not link.yanked)

        return (
            has_allowed_hash,
            yank_value,
            binary_preference,
            package.version,
            build_tag,
            pri,
        )

    def _is_link_hash_allowed_for_package(self, link: Link, package: Package) -> bool:
        if not link.hashes:
            return True

        link_hashes = {f"{name}:{h}" for name, h in link.hashes.items()}
        locked_hashes = {f["hash"] for f in package.files}

        return bool(link_hashes & locked_hashes)
