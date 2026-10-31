from pathlib import Path
from unittest.mock import patch

import pytest

from packaging.utils import canonicalize_name
from typing_extensions import override

from conda_lock import pypi_solver
from conda_lock._vendor.poetry.core.packages.dependency import Dependency
from conda_lock._vendor.poetry.core.packages.package import Package
from conda_lock._vendor.poetry.core.packages.utils.link import Link
from conda_lock._vendor.poetry.packages.direct_origin import DirectOrigin
from conda_lock._vendor.poetry.repositories.repository import Repository
from conda_lock._vendor.poetry.repositories.repository_pool import RepositoryPool
from conda_lock.lockfile.v2prelim.models import LockedDependency
from conda_lock.models.lock_spec import Dependency as LockDependency
from conda_lock.models.lock_spec import URLDependency, VersionedDependency
from conda_lock.src_parser.pyproject_toml import parse_pyproject_toml


WHEEL_URL = "https://example.invalid/idna-3.10-py3-none-any.whl"
SHA256 = "0" * 64


class FixtureRepository(Repository):
    @override
    def find_links_for_package(self, package: Package) -> list[Link]:
        return [
            Link(
                f"https://example.invalid/{package.name}-{package.version}"
                f"-py3-none-any.whl#sha256={SHA256}"
            )
        ]


@pytest.fixture
def mapping_url(tmp_path: Path) -> str:
    mapping = tmp_path / "mapping.json"
    mapping.write_text("{}")
    return str(mapping)


@pytest.mark.parametrize("requests_constraint", ["*", ">=2.30"])
def test_relock_preserves_url_version_and_dependents(
    mapping_url: str, requests_constraint: str
) -> None:
    modern = Package("requests", "2.32.0")
    modern.add_dependency(Dependency("idna", ">=2.5,<4"))
    # This older release has no idna requirement. A bogus locked idna version
    # of 0.0.0 used to make an unconstrained re-lock silently select it.
    ancient = Package("requests", "2.15.1")
    pool = RepositoryPool([FixtureRepository("PyPI", [modern, ancient])])
    specs: dict[str, LockDependency] = {
        "idna": URLDependency(
            name="idna", manager="pip", url=WHEEL_URL, hashes=[f"sha256:{SHA256}"]
        ),
        "requests": VersionedDependency(
            name="requests", manager="pip", version=requests_constraint
        ),
    }

    def direct_package(url: str) -> Package:
        return Package("idna", "3.10", source_type="url", source_url=url)

    def solve(locked: dict[str, LockedDependency]) -> dict[str, LockedDependency]:
        return pypi_solver.solve_pypi(
            pip_specs=specs,
            use_latest=[],
            pip_locked=locked,
            conda_locked={},
            python_version="3.12.0",
            platform="linux-64",
            mapping_url=mapping_url,
        )

    with (
        patch.object(pypi_solver, "_prepare_repositories_pool", return_value=pool),
        patch.object(
            DirectOrigin,
            "get_package_from_url",
            side_effect=direct_package,
        ),
        patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Unexpected network access"),
        ),
    ):
        first = solve({})
        relocked = solve(first)

    for result in (first, relocked):
        assert {name: dep.version for name, dep in result.items()} == {
            "idna": "3.10",
            "requests": "2.32.0",
        }
        assert result["idna"].url == WHEEL_URL
        assert result["idna"].hash.sha256 == SHA256
    assert relocked["idna"].source == first["idna"].source


@pytest.mark.parametrize("style", ["poetry", "pep621"])
@pytest.mark.parametrize("requested_extra", [False, True])
def test_git_extras_reach_solver(
    tmp_path: Path, mapping_url: str, style: str, requested_extra: bool
) -> None:
    git_url = "https://example.invalid/fixture-vcs.git"
    revision = "a" * 40
    if style == "poetry":
        extras = ', extras = ["excel"]' if requested_extra else ""
        content = (
            '[build-system]\nbuild-backend = "poetry.core.masonry.api"\n'
            "[tool.poetry.dependencies]\n"
            f'fixture-vcs = {{git = "{git_url}", rev = "v1"{extras}}}\n'
        )
    else:
        extras = "[excel]" if requested_extra else ""
        content = (
            '[project]\nname = "example"\n'
            f'dependencies = ["fixture-vcs{extras} @ git+{git_url}@v1"]\n'
            '[tool.conda-lock]\ndefault-non-conda-source = "pip"\n'
        )
    source = tmp_path / "pyproject.toml"
    source.write_text(content)
    spec = parse_pyproject_toml(source, platforms=["linux-64"], mapping_url=mapping_url)
    vcs = next(
        dep for dep in spec.dependencies["linux-64"] if dep.name == "fixture-vcs"
    )

    package = Package(
        "fixture-vcs",
        "1.0",
        source_type="git",
        source_url=git_url,
        source_reference="v1",
        source_resolved_reference=revision,
    )
    excel = Dependency("fixture-excel", ">=1", optional=True)
    package.add_dependency(excel)
    package.extras = {canonicalize_name("excel"): [excel]}
    pool = RepositoryPool(
        [FixtureRepository("PyPI", [Package("fixture-excel", "1.0")])]
    )
    with (
        patch.object(pypi_solver, "_prepare_repositories_pool", return_value=pool),
        patch.object(DirectOrigin, "get_package_from_vcs", return_value=package),
        patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Unexpected network access"),
        ),
    ):
        result = pypi_solver.solve_pypi(
            pip_specs={vcs.name: vcs},
            use_latest=[],
            pip_locked={},
            conda_locked={},
            python_version="3.12.0",
            platform="linux-64",
            mapping_url=mapping_url,
        )

    assert set(result) == (
        {"fixture-vcs", "fixture-excel"} if requested_extra else {"fixture-vcs"}
    )
    assert result["fixture-vcs"].url == f"git+{git_url}@{revision}"
    assert result["fixture-vcs"].hash.sha256 == revision
