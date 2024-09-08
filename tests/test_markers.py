from pathlib import Path

import pytest

from conda_lock.src_parser import make_lock_spec


ENVIRONMENT_YAML = """
channels:
    - conda-forge
    - nodefaults
dependencies:
    - pip
    - pip:
        - cowsay; sys_platform == 'darwin'
platforms:
    - osx-64
    - osx-arm64
    - linux-64
"""

POETRY_PYPROJECT = """
[tool.poetry]
name = "conda-lock-test-poetry"
version = "0.0.1"
description = ""
authors = ["conda-lock"]

[tool.poetry.dependencies]
python = "^3.9"
cowsay = {version = "*", markers = "sys_platform == 'darwin'"}

[build-system]
requires = ["poetry>=0.12"]
build-backend = "poetry.masonry.api"

[tool.conda-lock]
platforms = [
    "osx-64",
    "osx-arm64",
    "linux-64",
]
"""

HATCH_PYPROJECT = """
[build-system]
requires = ["hatchling >=1.25.0,<2"]
build-backend = "hatchling.build"

[project]
name = "conda-lock-test-hatch"
version = "0.0.1"
dependencies = ["cowsay; sys_platform == 'darwin'"]

[tool.conda-lock]
platforms = [
    "osx-64",
    "osx-arm64",
    "linux-64",
]
"""


@pytest.fixture(
    params=[
        (ENVIRONMENT_YAML, "environment.yml"),
        (POETRY_PYPROJECT, "pyproject.toml"),
        (HATCH_PYPROJECT, "pyproject.toml"),
    ],
    ids=["environment.yml", "poetry", "hatch"],
)
def cowsay_src_file(request, tmp_path: Path):
    contents, filename = request.param
    src_file = tmp_path / filename
    src_file.write_text(contents)
    return src_file


def test_sys_platform_marker(cowsay_src_file):
    lock_spec = make_lock_spec(src_files=[cowsay_src_file])
    dependencies = lock_spec.dependencies
    platform_has_cowsay = {
        platform: any(dep.name == "cowsay" for dep in platform_deps)
        for platform, platform_deps in dependencies.items()
    }
    assert platform_has_cowsay == {
        "osx-64": True,
        "osx-arm64": True,
        "linux-64": False,
    }
