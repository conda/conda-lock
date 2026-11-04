from __future__ import annotations

from typing import TYPE_CHECKING

from conda_lock._vendor.poetry.core.utils.helpers import readme_content_type


if TYPE_CHECKING:
    from pathlib import Path

    from packaging.utils import NormalizedName

    from conda_lock._vendor.poetry.core.packages.project_package import ProjectPackage


class Metadata:
    metadata_version = "2.4"
    # version 1.0
    name: str | None = None
    version: str
    platforms: tuple[str, ...] = ()
    supported_platforms: tuple[str, ...] = ()
    summary: str | None = None
    description: str | None = None
    keywords: str | None = None
    home_page: str | None = None
    download_url: str | None = None
    author: str | None = None
    author_email: str | None = None
    license: str | None = None
    # version 1.1
    classifiers: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    obsoletes: tuple[str, ...] = ()
    # version 1.2
    maintainer: str | None = None
    maintainer_email: str | None = None
    requires_python: str | None = None
    requires_external: tuple[str, ...] = ()
    requires_dist: list[str] = []  # noqa: RUF012
    provides_dist: tuple[str, ...] = ()
    obsoletes_dist: tuple[str, ...] = ()
    project_urls: tuple[str, ...] = ()

    # Version 2.1
    description_content_type: str | None = None
    provides_extra: list[NormalizedName] = []  # noqa: RUF012

    # Version 2.4
    license_expression: str | None = None
    license_files: tuple[str, ...] = ()

    @classmethod
    def from_package(cls, package: ProjectPackage) -> Metadata:
        from conda_lock._vendor.poetry.core.version.helpers import format_python_constraint

        meta = cls()

        meta.name = package.pretty_name
        meta.version = package.version.to_string()
        meta.summary = package.description
        if package.readme_content:
            meta.description = package.readme_content
        elif package.readmes:
            descriptions = []
            for readme in package.readmes:
                try:
                    descriptions.append(readme.read_text(encoding="utf-8"))
                except FileNotFoundError as e:
                    raise FileNotFoundError(
                        f"Readme path `{readme}` does not exist."
                    ) from e
                except IsADirectoryError as e:
                    raise IsADirectoryError(
                        f"Readme path `{readme}` is a directory."
                    ) from e
                except PermissionError as e:
                    raise PermissionError(
                        f"Readme path `{readme}` is not readable."
                    ) from e
            meta.description = "\n".join(descriptions)

        meta.keywords = ",".join(package.keywords)
        meta.home_page = package.homepage or package.repository_url
        meta.author = package.author_name
        meta.author_email = package.author_email

        if package.license:
            assert package.license_expression is None
            meta.license = package.license.id

        if package.license_expression:
            assert package.license is None
            meta.license_expression = package.license_expression

        meta.license_files = tuple(
            sorted(f.as_posix() for f in cls._license_files_from_package(package))
        )

        meta.classifiers = tuple(package.all_classifiers)

        # Version 1.2
        meta.maintainer = package.maintainer_name
        meta.maintainer_email = package.maintainer_email

        # Requires python
        if package.requires_python != "*":
            meta.requires_python = package.requires_python
        elif package.python_versions != "*":
            meta.requires_python = format_python_constraint(package.python_constraint)

        meta.requires_dist = [
            d.to_pep_508()
            for d in package.requires
            if not d.is_optional() or d.in_extras
        ]

        # Version 2.1
        if package.readme_content_type:
            meta.description_content_type = package.readme_content_type
        elif package.readmes:
            meta.description_content_type = readme_content_type(package.readmes[0])

        meta.provides_extra = list(package.extras)

        meta.project_urls = tuple(
            f"{name}, {url}" for name, url in package.urls.items()
        )

        return meta

    @classmethod
    def _license_files_from_package(cls, package: ProjectPackage) -> set[Path]:
        assert package.root_dir is not None, (
            "root_dir should always be set for the project package"
        )

        license_files: set[Path] = set()
        if isinstance(package.license_files, tuple):
            for glob_pattern in package.license_files:
                license_files_for_glob = {
                    license_file.relative_to(package.root_dir)
                    for license_file in sorted(package.root_dir.glob(glob_pattern))
                    if license_file.is_file()
                }
                if not license_files_for_glob:
                    # Build tools MUST raise an error if any individual user-specified
                    # pattern does not match at least one file.
                    # https://peps.python.org/pep-0639/#add-license-files-key
                    raise RuntimeError(
                        f"No files found for license file glob pattern '{glob_pattern}'"
                    )
                license_files.update(license_files_for_glob)
        else:
            if package.license_files:
                license_files.add(package.root_dir / package.license_files)
            # default handling
            include_files_patterns = {"COPYING*", "LICEN[SC]E*", "AUTHORS*", "NOTICE*"}

            for pattern in include_files_patterns:
                license_files.update(package.root_dir.glob(pattern))

            license_files.update(package.root_dir.joinpath("LICENSES").glob("**/*"))

            license_files = {
                f.relative_to(package.root_dir) for f in license_files if f.is_file()
            }
        return license_files
