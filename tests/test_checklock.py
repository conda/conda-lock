import pytest
import yaml

from conda_lock.check_lockfile import check_lockfile
from conda_lock.lookup import DEFAULT_MAPPING_URL


@pytest.fixture
def lock_lockfile(tmp_path):
    lockfile_content = {
        "version": 2,
        "metadata": {
            "platforms": ["linux-64"],
            "content_hash": {
                "linux-64": "some-hash"
            },
            "channels": [{"url": "conda-forge", "used_env_vars": []}],
            "sources": ["pyproject.toml"],
        },
        "package": [
            {
                "name": "python",
                "version": "3.10.4",
                "manager": "conda",
                "platform": "linux-64",
                "dependencies": {},
                "url": "https://conda.anaconda.org/conda-forge/linux-64/python-3.10.4-h4a18420_0_cpython.tar.bz2",
                "hash": {"md5": "1234"},
                "categories": ["main"],
            },
            {
                "name": "requests",
                "version": "2.28.1",
                "manager": "conda",
                "platform": "linux-64",
                "dependencies": {"python": ">=3.7"},
                "url": "https://conda.anaconda.org/conda-forge/linux-64/requests-2.28.1-pyhd8ed1ab_0.tar.bz2",
                "hash": {"md5": "5678"},
                "categories": ["main"],
            },
            {
                "name": "pytest",
                "version": "7.1.2",
                "manager": "conda",
                "platform": "linux-64",
                "dependencies": {"python": ">=3.7"},
                "url": "https://conda.anaconda.org/conda-forge/linux-64/pytest-7.1.2-py310hff52083_0.tar.bz2",
                "hash": {"md5": "9abc"},
                "categories": ["dev"],
            },
        ],
    }
    lockfile_path = tmp_path / "conda-lock.yml"
    with open(lockfile_path, "w") as f:
        yaml.dump(lockfile_content, f)
    return lockfile_path


@pytest.fixture
def pyproject_toml(tmp_path):
    pyproject_content = """
[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"

[tool.poetry]
name = "my-project"
version = "0.1.0"
description = ""
authors = ["Your Name <you@example.com>"]

[tool.poetry.dependencies]
python = ">=3.10"
requests = "^2.28.1"

[tool.poetry.group.dev.dependencies]
pytest = "^7.1.2"

[tool.conda-lock]
dependencies = { "python" = { version = ">=3.10", manager = "conda" }, "requests" = { version = "^2.28.1", manager = "conda" } }
dev-dependencies = { "pytest" = { version = "^7.1.2", manager = "conda" } }
"""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(pyproject_content)
    return pyproject_path


def test_check_success(lock_lockfile, pyproject_toml):
    assert check_lockfile(lock_lockfile, [pyproject_toml], DEFAULT_MAPPING_URL)


def test_check_missing_package(lock_lockfile, tmp_path):
    pyproject_content = """
[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"

[tool.conda-lock]
dependencies = { python = ">=3.10", requests = ">=2.28.1,<3.0.0", numpy = "*" }
"""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(pyproject_content)

    assert not check_lockfile(lock_lockfile, [pyproject_path], DEFAULT_MAPPING_URL)


def test_check_extra_package(lock_lockfile, tmp_path):
    pyproject_content = """
[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"

[tool.conda-lock]
dependencies = { python = ">=3.10" }
"""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(pyproject_content)

    assert not check_lockfile(lock_lockfile, [pyproject_path], DEFAULT_MAPPING_URL)


def test_check_filter_extras(lock_lockfile, pyproject_toml):
    is_valid = check_lockfile(
        lock_lockfile,
        [pyproject_toml],
        DEFAULT_MAPPING_URL,
        filter_categories=True,
        include_dev_dependencies=False,
    )
    assert is_valid


def test_check_with_cuda(lock_lockfile, pyproject_toml):
    assert check_lockfile(
        lock_lockfile, [pyproject_toml], DEFAULT_MAPPING_URL, with_cuda="11.4"
    )


def test_check_inconsistent_category(lock_lockfile, pyproject_toml):
    with open(lock_lockfile) as f:
        lock_data = yaml.safe_load(f)

    # make lockfile inconsistent by changing a category
    for pkg in lock_data["package"]:
        if pkg["name"] == "pytest":
            pkg["categories"] = ["main"]

    inconsistent_lockfile_path = lock_lockfile.parent / "inconsistent-lock.yml"
    with open(inconsistent_lockfile_path, "w") as f:
        yaml.dump(lock_data, f)

    assert not check_lockfile(
        inconsistent_lockfile_path,
        [pyproject_toml],
        DEFAULT_MAPPING_URL,
        include_dev_dependencies=False,
    )


def test_check_extra_package_in_category(lock_lockfile, pyproject_toml):
    with open(lock_lockfile) as f:
        lock_data = yaml.safe_load(f)

    # Add an extra package to the 'main' category
    lock_data["package"].append(
        {
            "name": "extra-package",
            "version": "1.0.0",
            "manager": "conda",
            "platform": "linux-64",
            "dependencies": {},
            "url": "https://conda.anaconda.org/conda-forge/linux-64/extra-package-1.0.0-0.tar.bz2",
            "hash": {"md5": "abcd"},
            "categories": ["main"],
        }
    )

    inconsistent_lockfile_path = lock_lockfile.parent / "extra-package-lock.yml"
    with open(inconsistent_lockfile_path, "w") as f:
        yaml.dump(lock_data, f)

    assert not check_lockfile(
        inconsistent_lockfile_path,
        [pyproject_toml],
        DEFAULT_MAPPING_URL,
    )


def test_check_missing_package_in_category(lock_lockfile, pyproject_toml):
    with open(lock_lockfile) as f:
        lock_data = yaml.safe_load(f)

    # Remove a package from the 'main' category
    lock_data["package"] = [p for p in lock_data["package"] if p["name"] != "requests"]

    inconsistent_lockfile_path = lock_lockfile.parent / "missing-package-lock.yml"
    with open(inconsistent_lockfile_path, "w") as f:
        yaml.dump(lock_data, f)

    assert not check_lockfile(
        inconsistent_lockfile_path,
        [pyproject_toml],
        DEFAULT_MAPPING_URL,
    )


def test_check_no_common_platforms(lock_lockfile, pyproject_toml):
    assert not check_lockfile(
        lock_lockfile,
        [pyproject_toml],
        DEFAULT_MAPPING_URL,
        platform_overrides=["osx-64"],
    )