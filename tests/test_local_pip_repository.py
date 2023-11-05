import base64
import os
import re
import shutil
import tarfile

from pathlib import Path
from urllib.parse import urlparse

import pytest

from conda_lock.conda_lock import DEFAULT_LOCKFILE_NAME, run_lock
from conda_lock.lockfile import parse_conda_lock_file
from tests.test_conda_lock import clone_test_dir
from tests.test_pip_repositories import private_package_tar


_PRIVATE_REPO_ROOT = """<!DOCTYPE html>
<html>
  <body>
    <a href="fake-private-package/index.html">fake-private-package</a>
  </body>
</html>
"""

_PRIVATE_REPO_PACKAGE = """<!DOCTYPE html>
<html>
  <body>
    <a href="file://$PYPI_FILE_URL/fake-private-package/fake-private-package-1.0.0.tar.gz">fake-private-package-1.0.0.tar.gz</a>
  </body>
</html>
"""

_PRIVATE_PACKAGE_SDIST_PATH = (
    Path(__file__).parent / "test-pip-repositories" / "fake-private-package-1.0.0"
)


@pytest.fixture(autouse=True)
def create_local_private_pypi(private_package_tar: Path, tmp_path: Path):  # noqa: F811
    repo_dir = tmp_path / "repo"
    pkg_dir = repo_dir / "fake-private-package"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    tar_path = pkg_dir / private_package_tar.name
    shutil.copy(private_package_tar, tar_path)

    with open(repo_dir / "index.html", "w") as repo_file:
        repo_file.write(_PRIVATE_REPO_ROOT)
    _repo_package_html = _PRIVATE_REPO_PACKAGE.replace("$PYPI_FILE_URL", str(repo_dir))
    with open(pkg_dir / "index.html", "w") as pkg_file:
        pkg_file.write(_repo_package_html)
    yield


def use_local_file_url_in_environment_yaml(environment_file: Path, repo_dir: Path):
    with environment_file.open("r+") as env_fp:
        templated_env_yml = env_fp.read()
        env_yml = templated_env_yml.replace("$PYPI_FILE_URL", str(repo_dir))
        env_fp.seek(0)
        env_fp.write(env_yml)
        env_fp.truncate()


def test_it_installs_packages_from_private_pip_repository_on_local_disk(
    monkeypatch: "pytest.MonkeyPatch",
    conda_exe: str,
    tmp_path: Path,
):
    # GIVEN an environment.yaml with custom pip repositories
    directory = clone_test_dir("test-local-pip-repository", tmp_path)
    monkeypatch.chdir(directory)
    repo_dir = tmp_path / "repo"
    environment_file = directory / "environment.yaml"
    assert environment_file.exists(), list(directory.iterdir())
    use_local_file_url_in_environment_yaml(environment_file, repo_dir)

    # WHEN I create the lockfile
    run_lock([directory / "environment.yaml"], conda_exe=conda_exe)

    # THEN the lockfile is generated correctly
    lockfile_path = directory / DEFAULT_LOCKFILE_NAME
    assert lockfile_path.exists(), list(directory.iterdir())
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
