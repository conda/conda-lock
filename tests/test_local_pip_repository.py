import shutil

from pathlib import Path
from urllib.parse import urlparse

import pytest

from conda_lock.conda_lock import DEFAULT_LOCKFILE_NAME, run_lock
from conda_lock.lockfile import parse_conda_lock_file
from tests.test_conda_lock import clone_test_dir

# This is a fixture, so we need to import it:
from tests.test_pip_repositories import private_package_tar  # pyright: ignore


_PRIVATE_REPO_ROOT_INDEX = """<!DOCTYPE html>
<html>
  <body>
    <a href="fake-private-package/index.html">fake-private-package</a>
  </body>
</html>
"""

_PRIVATE_REPO_PACKAGE_INDEX = """<!DOCTYPE html>
<html>
  <body>
    <a href="file://$PYPI_FILE_URL/fake-private-package/fake-private-package-1.0.0.tar.gz">fake-private-package-1.0.0.tar.gz</a>
  </body>
</html>
"""


@pytest.fixture
def local_private_pypi(private_package_tar: Path, tmp_path: Path):  # noqa: F811
    """
    Create a local package index with the following directory structure:
    tmp_path
    └── repo
        ├── fake-private-package
        │   ├── fake-private-package-1.0.0.tar.gz
        │   └── index.html
        └── index.html
    """
    repo_dir = tmp_path / "repo"
    pkg_dir = repo_dir / "fake-private-package"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    tar_path = pkg_dir / private_package_tar.name
    shutil.copy(private_package_tar, tar_path)

    with open(repo_dir / "index.html", "w") as repo_index:
        repo_index.write(_PRIVATE_REPO_ROOT_INDEX)

    _repo_package_index_html = _PRIVATE_REPO_PACKAGE_INDEX.replace(
        "$PYPI_FILE_URL", str(repo_dir)
    )
    with open(pkg_dir / "index.html", "w") as pkg_index:
        pkg_index.write(_repo_package_index_html)
    return repo_dir


@pytest.fixture
def local_pip_repository_environment_file(
    local_private_pypi: Path, tmp_path: Path
) -> Path:
    environment_file = (
        clone_test_dir("test-local-pip-repository", tmp_path) / "environment.yaml"
    )
    templated_env_yml = environment_file.read_text()
    env_yml = templated_env_yml.replace("$PYPI_FILE_URL", str(local_private_pypi))
    environment_file.write_text(env_yml)
    return environment_file


def test_it_installs_packages_from_private_pip_repository_on_local_disk(
    monkeypatch: "pytest.MonkeyPatch",
    conda_exe: str,
    tmp_path: Path,
    local_pip_repository_environment_file: Path,
):
    # WHEN I create the lockfile
    monkeypatch.chdir(tmp_path)
    environment_file = local_pip_repository_environment_file
    run_lock([environment_file], conda_exe=conda_exe)

    # THEN the lockfile is generated correctly
    lockfile_path = tmp_path / DEFAULT_LOCKFILE_NAME
    assert lockfile_path.exists(), list(tmp_path.iterdir())
    lockfile = parse_conda_lock_file(lockfile_path)
    lockfile_content = lockfile_path.read_text(encoding="utf-8")
    packages = {package.name: package for package in lockfile.package}

    # AND the private package is in the lockfile
    private_package = packages.get("fake-private-package")
    assert private_package, lockfile_content

    # AND the private package was installed from the local repository
    assert private_package.url.startswith("file://")

    # AND the six package is in the lockfile
    package = packages.get("six")
    assert package, lockfile_content

    package_url = urlparse(package.url)
    # AND the package was sourced from pypi
    assert package_url.hostname == "files.pythonhosted.org", (
        "Package was fetched from incorrect host. See full lock-file:\n"
        + lockfile_content
    )
