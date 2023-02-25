import contextlib
import datetime
import json
import logging
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import typing
import uuid

from glob import glob
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock
from urllib.parse import urldefrag, urlsplit

import filelock
import pytest
import yaml

from flaky import flaky
from freezegun import freeze_time

from conda_lock import __version__, pypi_solver
from conda_lock._vendor.conda.models.match_spec import MatchSpec
from conda_lock.conda_lock import (
    DEFAULT_FILES,
    DEFAULT_LOCKFILE_NAME,
    _add_auth_to_line,
    _add_auth_to_lockfile,
    _extract_domain,
    _strip_auth_from_line,
    _strip_auth_from_lockfile,
    create_lockfile_from_spec,
    default_virtual_package_repodata,
    determine_conda_executable,
    extract_input_hash,
    main,
    make_lock_spec,
    run_lock,
)
from conda_lock.conda_solver import extract_json_object, fake_conda_environment
from conda_lock.errors import (
    ChannelAggregationError,
    MissingEnvVarError,
    PlatformValidationError,
)
from conda_lock.invoke_conda import (
    PathLike,
    _ensureconda,
    is_micromamba,
    reset_conda_pkgs_dir,
)
from conda_lock.lockfile import (
    HashModel,
    LockedDependency,
    MetadataOption,
    parse_conda_lock_file,
)
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import Selectors, VersionedDependency
from conda_lock.pypi_solver import parse_pip_requirement, solve_pypi
from conda_lock.src_parser import (
    DEFAULT_PLATFORMS,
    LockSpecification,
    parse_meta_yaml_file,
)
from conda_lock.src_parser.aggregation import aggregate_lock_specs
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.pyproject_toml import (
    parse_pyproject_toml,
    poetry_version_to_conda_version,
)


if typing.TYPE_CHECKING:
    from tests.conftest import QuetzServerInfo


TEST_DIR = Path(__file__).parent


@pytest.fixture(autouse=True)
def logging_setup(caplog):
    caplog.set_level(logging.INFO)


@pytest.fixture
def reset_global_conda_pkgs_dir():
    reset_conda_pkgs_dir()


def clone_test_dir(name: str, tmp_path: Path) -> Path:
    test_dir = TEST_DIR.joinpath(name)
    assert test_dir.exists()
    assert test_dir.is_dir()
    if sys.version_info >= (3, 8):
        shutil.copytree(test_dir, tmp_path, dirs_exist_ok=True)
    else:
        from distutils.dir_util import copy_tree

        copy_tree(str(test_dir), str(tmp_path))
    return tmp_path


@contextlib.contextmanager
def install_lock():
    with filelock.FileLock(str(TEST_DIR.joinpath("install.lock"))):
        yield


@pytest.fixture
def gdal_environment(tmp_path: Path):
    x = clone_test_dir("gdal", tmp_path).joinpath("environment.yml")
    assert x.exists()
    return x


@pytest.fixture
def filter_conda_environment(tmp_path: Path):
    x = clone_test_dir("test-env-filter-platform", tmp_path).joinpath("environment.yml")
    assert x.exists()
    return x


@pytest.fixture
def pip_environment(tmp_path: Path):
    return clone_test_dir("test-pypi-resolve", tmp_path).joinpath("environment.yml")


@pytest.fixture
def pip_environment_different_names_same_deps(tmp_path: Path):
    return clone_test_dir("test-pypi-resolve-namediff", tmp_path).joinpath(
        "environment.yml"
    )


@pytest.fixture
def pip_environment_regression_gh155(tmp_path: Path):
    return clone_test_dir("test-pypi-resolve-gh155", tmp_path).joinpath(
        "environment.yml"
    )


@pytest.fixture
def pip_local_package_environment(tmp_path: Path):
    return clone_test_dir("test-local-pip", tmp_path).joinpath("environment.yml")


@pytest.fixture
def zlib_environment(tmp_path: Path):
    return clone_test_dir("zlib", tmp_path).joinpath("environment.yml")


@pytest.fixture
def input_hash_zlib_environment(tmp_path: Path):
    return clone_test_dir("test-input-hash-zlib", tmp_path).joinpath("environment.yml")


@pytest.fixture
def blas_mkl_environment(tmp_path: Path):
    return clone_test_dir("test-environment-blas-mkl", tmp_path).joinpath(
        "environment.yml"
    )


@pytest.fixture
def meta_yaml_environment(tmp_path: Path):
    return clone_test_dir("test-recipe", tmp_path).joinpath("meta.yaml")


@pytest.fixture
def poetry_pyproject_toml(tmp_path: Path):
    return clone_test_dir("test-poetry", tmp_path).joinpath("pyproject.toml")


@pytest.fixture
def poetry_pyproject_toml_no_pypi(tmp_path: Path):
    return clone_test_dir("test-poetry-no-pypi", tmp_path).joinpath("pyproject.toml")


@pytest.fixture
def flit_pyproject_toml(tmp_path: Path):
    return clone_test_dir("test-flit", tmp_path).joinpath("pyproject.toml")


@pytest.fixture
def pdm_pyproject_toml(tmp_path: Path):
    return clone_test_dir("test-pdm", tmp_path).joinpath("pyproject.toml")


@pytest.fixture
def channel_inversion(tmp_path: Path):
    """Path to an environment.yaml that has a hardcoded channel in one of the dependencies"""
    return clone_test_dir("test-channel-inversion", tmp_path).joinpath(
        "environment.yaml"
    )


@pytest.fixture
def env_with_uppercase_pip(tmp_path: Path):
    """Path to an environment.yaml that has a hardcoded channel in one of the dependencies"""
    return clone_test_dir("test-uppercase-pip", tmp_path).joinpath("environment.yml")


@pytest.fixture
def git_metadata_zlib_environment(tmp_path: Path):
    return clone_test_dir("zlib", tmp_path).joinpath("environment.yml")


@pytest.fixture(
    scope="function",
    params=[
        pytest.param(True, id="--dev-dependencies"),
        pytest.param(False, id="--no-dev-dependencies"),
    ],
)
def include_dev_dependencies(request: Any) -> bool:
    return request.param


@pytest.fixture(
    scope="session",
    params=[
        pytest.param("conda"),
        pytest.param("mamba"),
        pytest.param("micromamba"),
    ],
)
def _conda_exe_type(request: Any) -> str:
    "Internal fixture to iterate over"
    return request.param


@pytest.fixture(scope="session")
@typing.no_type_check
def conda_exe(_conda_exe_type: str) -> PathLike:
    kwargs = dict(
        mamba=False,
        micromamba=False,
        conda=False,
        conda_exe=False,
    )
    kwargs[_conda_exe_type] = True
    _conda_exe = _ensureconda(**kwargs)

    if _conda_exe is not None:
        return _conda_exe
    pytest.skip(f"{_conda_exe_type} is not installed")


JSON_FIELDS: Dict[str, str] = {"json_unique_field": "test1", "common_field": "test2"}

YAML_FIELDS: Dict[str, str] = {"yaml_unique_field": "test3", "common_field": "test4"}

EXPECTED_CUSTOM_FIELDS: Dict[str, str] = {
    "json_unique_field": "test1",
    "yaml_unique_field": "test3",
    "common_field": "test4",
}


@pytest.fixture
def custom_metadata_environment(tmp_path: Path):
    return clone_test_dir("zlib", tmp_path / "test-custom-metadata")


@pytest.fixture
def custom_yaml_metadata(custom_metadata_environment: Path) -> Path:
    outfile = custom_metadata_environment / "custom_metadata.yaml"
    with outfile.open("w") as out_yaml:
        yaml.dump(YAML_FIELDS, out_yaml)

    return outfile


@pytest.fixture
def custom_json_metadata(custom_metadata_environment: Path) -> Path:
    outfile = custom_metadata_environment / "custom_metadata.json"
    with outfile.open("w") as out_json:
        json.dump(JSON_FIELDS, out_json)

    return outfile


def test_lock_poetry_ibis(
    tmp_path: Path, mamba_exe: Path, monkeypatch: "pytest.MonkeyPatch"
):
    pyproject = clone_test_dir("test-poetry-ibis", tmp_path).joinpath("pyproject.toml")
    monkeypatch.chdir(pyproject.parent)

    extra_categories = {"test", "dev", "docs"}

    run_lock(
        [pyproject],
        conda_exe=str(mamba_exe),
        platforms=["linux-64"],
        extras={"test", "dev", "docs"},
        filter_categories=True,
    )
    lockfile = parse_conda_lock_file(pyproject.parent / DEFAULT_LOCKFILE_NAME)

    all_categories = set()

    for pkg in lockfile.package:
        all_categories.add(pkg.category)

    for desired_category in extra_categories:
        assert (
            desired_category in all_categories
        ), "Extra category not found in lockfile"


def test_parse_environment_file(gdal_environment: Path):
    res = parse_environment_file(gdal_environment, DEFAULT_PLATFORMS)
    assert all(
        x in res.dependencies
        for x in [
            VersionedDependency(
                name="python",
                manager="conda",
                version=">=3.7,<3.8",
            ),
            VersionedDependency(
                name="gdal",
                manager="conda",
                version="",
            ),
        ]
    )
    assert (
        VersionedDependency(
            name="toolz",
            manager="pip",
            version="*",
        )
        in res.dependencies
    )
    assert all(
        Channel.from_string(x) in res.channels for x in ["conda-forge", "defaults"]
    )


def test_parse_environment_file_with_pip(pip_environment: Path):
    res = parse_environment_file(pip_environment, DEFAULT_PLATFORMS)
    assert [dep for dep in res.dependencies if dep.manager == "pip"] == [
        VersionedDependency(
            name="requests-toolbelt",
            manager="pip",
            optional=False,
            category="main",
            extras=[],
            version="=0.9.1",
        )
    ]


def test_parse_env_file_with_filters_no_args(filter_conda_environment: Path):
    res = parse_environment_file(filter_conda_environment, None)
    assert all(x in res.platforms for x in ["osx-arm64", "osx-64", "linux-64"])
    assert res.channels == [Channel.from_string("conda-forge")]

    assert all(
        x in res.dependencies
        for x in [
            VersionedDependency(
                name="python",
                manager="conda",
                version="<3.11",
            ),
            VersionedDependency(
                name="clang_osx-arm64",
                manager="conda",
                version="",
                selectors=Selectors(platform=["osx-arm64"]),
            ),
            VersionedDependency(
                name="clang_osx-64",
                manager="conda",
                version="",
                selectors=Selectors(platform=["osx-64"]),
            ),
            VersionedDependency(
                name="gcc_linux-64",
                manager="conda",
                version=">=6",
                selectors=Selectors(platform=["linux-64"]),
            ),
        ]
    )


def test_parse_env_file_with_filters_defaults(filter_conda_environment: Path):
    res = parse_environment_file(filter_conda_environment, DEFAULT_PLATFORMS)
    assert all(x in res.platforms for x in DEFAULT_PLATFORMS)
    assert res.channels == [Channel.from_string("conda-forge")]

    assert all(
        x in res.dependencies
        for x in [
            VersionedDependency(
                name="python",
                manager="conda",
                version="<3.11",
            ),
            VersionedDependency(
                name="clang_osx-64",
                manager="conda",
                version="",
                selectors=Selectors(platform=["osx-64"]),
            ),
            VersionedDependency(
                name="gcc_linux-64",
                manager="conda",
                version=">=6",
                selectors=Selectors(platform=["linux-64"]),
            ),
        ]
    )


def test_choose_wheel() -> None:
    solution = solve_pypi(
        {
            "fastavro": VersionedDependency(
                name="fastavro",
                manager="pip",
                optional=False,
                category="main",
                extras=[],
                version="1.4.7",
            )
        },
        use_latest=[],
        pip_locked={},
        conda_locked={
            "python": LockedDependency.parse_obj(
                {
                    "name": "python",
                    "version": "3.9.7",
                    "manager": "conda",
                    "platform": "linux-64",
                    "dependencies": {},
                    "url": "",
                    "hash": {
                        "md5": "deadbeef",
                    },
                }
            )
        },
        python_version="3.9.7",
        platform="linux-64",
    )
    assert len(solution) == 1
    assert solution["fastavro"].hash == HashModel(
        sha256="a111a384a786b7f1fd6a8a8307da07ccf4d4c425084e2d61bae33ecfb60de405"
    )


@pytest.mark.parametrize(
    "requirement, parsed",
    [
        (
            "package-thingie1[foo]",
            {
                "name": "package-thingie1",
                "constraint": None,
                "extras": "foo",
                "url": None,
            },
        ),
        (
            "package[extra] @ https://foo.bar/package.whl#sha1=blerp",
            {
                "name": "package",
                "constraint": None,
                "extras": "extra",
                "url": "https://foo.bar/package.whl#sha1=blerp",
            },
        ),
        (
            "package[extra] = 2.1",
            {
                "name": "package",
                "constraint": "= 2.1",
                "extras": "extra",
                "url": None,
            },
        ),
        (
            "package[extra] == 2.1",
            {
                "name": "package",
                "constraint": "== 2.1",
                "extras": "extra",
                "url": None,
            },
        ),
        (
            "package[extra]===2.1",
            {
                "name": "package",
                "constraint": "===2.1",
                "extras": "extra",
                "url": None,
            },
        ),
        (
            "package[extra] >=2.1.*, <4.0",
            {
                "name": "package",
                "constraint": ">=2.1.*, <4.0",
                "extras": "extra",
                "url": None,
            },
        ),
        (
            "package[extra] >=0.8.0-alpha.2,<1.0.0.0",
            {
                "name": "package",
                "constraint": ">=0.8.0-alpha.2,<1.0.0.0",
                "extras": "extra",
                "url": None,
            },
        ),
    ],
)
def test_parse_pip_requirement(
    requirement: str, parsed: "Dict[str, str | None]"
) -> None:
    assert parse_pip_requirement(requirement) == parsed


def test_parse_meta_yaml_file(meta_yaml_environment: Path):
    res = parse_meta_yaml_file(meta_yaml_environment, ["linux-64", "osx-64"])
    specs = {dep.name: dep for dep in res.dependencies}
    assert all(x in specs for x in ["python", "numpy"])
    assert all(
        dep.selectors
        == Selectors(
            platform=None
        )  # Platform will be set to None if all dependencies are the same
        for dep in specs.values()
    )
    # Ensure that this dep specified by a python selector is ignored
    assert "enum34" not in specs
    # Ensure that this platform specific dep is included
    assert "zlib" in specs
    assert specs["pytest"].category == "dev"
    assert specs["pytest"].optional is True


def test_parse_poetry(poetry_pyproject_toml: Path):
    res = parse_pyproject_toml(
        poetry_pyproject_toml,
    )

    specs = {
        dep.name: typing.cast(VersionedDependency, dep) for dep in res.dependencies
    }

    assert specs["requests"].version == ">=2.13.0,<3.0.0"
    assert specs["toml"].version == ">=0.10"
    assert specs["sqlite"].version == "<3.34"
    assert specs["certifi"].version == ">=2019.11.28"
    assert specs["pytest"].version == ">=5.1.0,<5.2.0"
    assert specs["pytest"].optional is True
    assert specs["pytest"].category == "dev"
    assert specs["tomlkit"].version == ">=0.7.0,<1.0.0"
    assert specs["tomlkit"].optional is True
    assert specs["tomlkit"].category == "tomlkit"

    assert res.channels == [Channel.from_string("defaults")]


def test_parse_poetry_no_pypi(poetry_pyproject_toml_no_pypi: Path):
    res = parse_pyproject_toml(
        poetry_pyproject_toml_no_pypi,
    )
    assert res.allow_pypi_requests is False


def test_prepare_repositories_pool():
    def contains_pypi(pool):
        return any(repo.name == "PyPI" for repo in pool.repositories)

    assert contains_pypi(
        pypi_solver._prepare_repositories_pool(allow_pypi_requests=True)
    )
    assert not contains_pypi(
        pypi_solver._prepare_repositories_pool(allow_pypi_requests=False)
    )


def test_spec_poetry(poetry_pyproject_toml: Path):
    virtual_package_repo = default_virtual_package_repodata()
    with virtual_package_repo:
        spec = make_lock_spec(
            src_files=[poetry_pyproject_toml], virtual_package_repo=virtual_package_repo
        )
        deps = {d.name for d in spec.dependencies}
        assert "tomlkit" in deps
        assert "pytest" in deps
        assert "requests" in deps

        spec = make_lock_spec(
            src_files=[poetry_pyproject_toml],
            virtual_package_repo=virtual_package_repo,
            required_categories={"main", "dev"},
        )
        deps = {d.name for d in spec.dependencies}
        assert "tomlkit" not in deps
        assert "pytest" in deps
        assert "requests" in deps

        spec = make_lock_spec(
            src_files=[poetry_pyproject_toml],
            virtual_package_repo=virtual_package_repo,
            required_categories={"main"},
        )
        deps = {d.name for d in spec.dependencies}
        assert "tomlkit" not in deps
        assert "pytest" not in deps
        assert "requests" in deps


def test_parse_flit(flit_pyproject_toml: Path):
    res = parse_pyproject_toml(
        flit_pyproject_toml,
    )

    specs = {
        dep.name: typing.cast(VersionedDependency, dep) for dep in res.dependencies
    }

    assert specs["requests"].version == ">=2.13.0"
    assert specs["toml"].version == ">=0.10"
    assert specs["sqlite"].version == "<3.34"
    assert specs["certifi"].version == ">=2019.11.28"
    assert specs["pytest"].version == ">=5.1.0"
    assert specs["pytest"].optional is True
    assert specs["pytest"].category == "dev"

    assert specs["toml"].manager == "pip"

    assert res.channels == [Channel.from_string("defaults")]


def test_parse_pdm(pdm_pyproject_toml: Path):
    res = parse_pyproject_toml(
        pdm_pyproject_toml,
    )

    specs = {
        dep.name: typing.cast(VersionedDependency, dep) for dep in res.dependencies
    }

    # Base dependencies
    assert specs["requests"].version == ">=2.13.0"
    assert specs["toml"].version == ">=0.10"
    # conda-lock exclusives
    assert specs["sqlite"].version == "<3.34"
    assert specs["certifi"].version == ">=2019.11.28"
    # PEP 621 optional dependencies (show up in package metadata)
    assert specs["click"].version == ">=7.0"
    assert specs["click"].optional is True
    assert specs["click"].category == "cli"
    # PDM dev extras
    assert specs["pytest"].version == ">=5.1.0"
    assert specs["pytest"].optional is True
    assert specs["pytest"].category == "dev"
    # Conda channels
    assert res.channels == [Channel.from_string("defaults")]


def test_run_lock(
    monkeypatch: "pytest.MonkeyPatch", zlib_environment: Path, conda_exe: str
):
    monkeypatch.chdir(zlib_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock([zlib_environment], conda_exe=conda_exe)


def test_run_lock_with_input_metadata(
    monkeypatch: "pytest.MonkeyPatch", zlib_environment: Path, conda_exe: str
):
    monkeypatch.chdir(zlib_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock(
        [zlib_environment],
        conda_exe=conda_exe,
        metadata_choices=set(
            [
                MetadataOption.InputMd5,
                MetadataOption.InputSha,
            ]
        ),
    )
    lockfile = parse_conda_lock_file(zlib_environment.parent / DEFAULT_LOCKFILE_NAME)

    inputs_metadata = lockfile.metadata.inputs_metadata
    assert inputs_metadata is not None, "Inputs Metadata was None"
    print(inputs_metadata)
    assert (
        inputs_metadata["environment.yml"].md5 == "5473161eb8500056d793df7ac720a36f"
    ), "Input md5 didn't match expectation"
    expected_shasum = "1177fb37f73bebd39bba9e504cb03495136b1961126475a5839da2e878b2afda"
    assert (
        inputs_metadata["environment.yml"].sha256 == expected_shasum
    ), "Input shasum didn't match expectation"


def test_run_lock_with_time_metadata(
    monkeypatch: "pytest.MonkeyPatch", zlib_environment: Path, conda_exe: str
):
    TIME_DIR = TEST_DIR / "test-time-metadata"

    TIME_DIR.mkdir(exist_ok=True)
    monkeypatch.chdir(TIME_DIR)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    frozen_datetime = datetime.datetime(
        year=1, month=7, day=12, hour=15, minute=6, second=3
    )
    with freeze_time(frozen_datetime):
        run_lock(
            [zlib_environment],
            conda_exe=conda_exe,
            metadata_choices=set(
                [
                    MetadataOption.TimeStamp,
                ]
            ),
        )
    lockfile = parse_conda_lock_file(TIME_DIR / DEFAULT_LOCKFILE_NAME)

    time_metadata = lockfile.metadata.time_metadata
    assert time_metadata is not None, "Time metadata was None"
    assert (
        datetime.datetime.fromisoformat(time_metadata.created_at.rstrip("Z"))
        == frozen_datetime
    ), (
        "Datetime added to lockfile didn't match expectation based on timestamps at start and end"
        + " of test"
    )


def test_run_lock_with_git_metadata(
    monkeypatch: "pytest.MonkeyPatch",
    git_metadata_zlib_environment: Path,
    conda_exe: str,
):
    monkeypatch.chdir(git_metadata_zlib_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")

    import git

    try:
        repo = git.Repo(search_parent_directories=True)  # type: ignore
    except git.exc.InvalidGitRepositoryError:  # type: ignore
        repo = git.Repo.init()  # type: ignore
        repo.index.add([git_metadata_zlib_environment])
        repo.index.commit(
            "temporary commit for running via github actions without failure"
        )
    if repo.config_reader().has_section("user"):
        current_user_name = repo.config_reader().get_value("user", "name", None)
        current_user_email = repo.config_reader().get_value("user", "email", None)
    else:
        current_user_name = None
        current_user_email = None

    if current_user_name is None:
        repo.config_writer().set_value("user", "name", "my_test_username").release()
    if current_user_email is None:
        repo.config_writer().set_value("user", "email", "my_test_email").release()
    run_lock(
        [git_metadata_zlib_environment],
        conda_exe=conda_exe,
        metadata_choices=set(
            [
                MetadataOption.GitSha,
                MetadataOption.GitUserName,
                MetadataOption.GitUserEmail,
            ]
        ),
    )
    lockfile = parse_conda_lock_file(
        git_metadata_zlib_environment.parent / DEFAULT_LOCKFILE_NAME
    )

    assert (
        lockfile.metadata.git_metadata is not None
    ), "Git metadata was None, should be some value"
    assert (
        lockfile.metadata.git_metadata.git_user_name is not None
    ), "Git metadata user.name was None, should be some value"
    assert (
        lockfile.metadata.git_metadata.git_user_email is not None
    ), "Git metadata user.email was None, should be some value"
    if current_user_name is None:
        config = repo.config_writer()
        config.remove_option("user", "name")
        config.release()
    if current_user_email is None:
        config = repo.config_writer()
        config.remove_option("user", "email")
        config.release()


def test_run_lock_with_custom_metadata(
    monkeypatch: "pytest.MonkeyPatch",
    custom_metadata_environment: Path,
    custom_yaml_metadata: Path,
    custom_json_metadata: Path,
    conda_exe: str,
):
    monkeypatch.chdir(custom_yaml_metadata.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock(
        [custom_metadata_environment / "environment.yml"],
        conda_exe=conda_exe,
        metadata_yamls=[custom_json_metadata, custom_yaml_metadata],
    )
    lockfile = parse_conda_lock_file(
        custom_yaml_metadata.parent / DEFAULT_LOCKFILE_NAME
    )

    assert (
        lockfile.metadata.custom_metadata is not None
    ), "Custom metadata was None unexpectedly"
    assert (
        lockfile.metadata.custom_metadata == EXPECTED_CUSTOM_FIELDS
    ), "Custom metadata didn't get written as expected"


def test_run_lock_blas_mkl(
    monkeypatch: "pytest.MonkeyPatch", blas_mkl_environment: Path, conda_exe: str
):
    monkeypatch.chdir(blas_mkl_environment.parent)
    run_lock([blas_mkl_environment], conda_exe=conda_exe)


@pytest.fixture
def update_environment(tmp_path: Path) -> Path:
    return clone_test_dir("test-update", tmp_path).joinpath(
        "environment-postupdate.yml"
    )


@flaky
@pytest.mark.timeout(120)
def test_run_lock_with_update(
    monkeypatch: "pytest.MonkeyPatch",
    update_environment: Path,
    conda_exe: str,
    _conda_exe_type: str,
):
    if platform.system().lower() == "windows":
        if _conda_exe_type == "conda":
            pytest.skip(
                reason="this test just takes too long on windows, due to the slow conda solver"
            )
        if _conda_exe_type == "micromamba":
            pytest.skip(
                reason="for some unknown reason this segfaults on windows when using xdist in ci"
            )

    monkeypatch.chdir(update_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    pre_environment = update_environment.parent / "environment-preupdate.yml"
    run_lock([pre_environment], conda_exe="mamba")
    # files should be ready now
    run_lock([pre_environment], conda_exe=conda_exe, update=["pydantic"])
    pre_lock = {
        p.name: p
        for p in parse_conda_lock_file(
            update_environment.parent / DEFAULT_LOCKFILE_NAME
        ).package
    }
    run_lock([update_environment], conda_exe=conda_exe, update=["pydantic"])
    post_lock = {
        p.name: p
        for p in parse_conda_lock_file(
            update_environment.parent / DEFAULT_LOCKFILE_NAME
        ).package
    }
    assert pre_lock["pydantic"].version == "1.7"
    assert post_lock["pydantic"].version == "1.9.0"
    assert pre_lock["flask"].version.startswith("1.")
    assert post_lock["flask"].version == pre_lock["flask"].version


def test_run_lock_with_locked_environment_files(
    monkeypatch: "pytest.MonkeyPatch", update_environment: Path, conda_exe: str
):
    """run_lock() with default args uses source files from lock"""
    monkeypatch.chdir(update_environment.parent)
    pre_environment = update_environment.parent / "environment-preupdate.yml"
    run_lock([pre_environment], conda_exe="mamba")
    make_lock_files = MagicMock()
    monkeypatch.setattr("conda_lock.conda_lock.make_lock_files", make_lock_files)
    run_lock(DEFAULT_FILES, conda_exe=conda_exe, update=["pydantic"])
    if sys.version_info < (3, 8):
        # backwards compat
        src_files = make_lock_files.call_args_list[0][1]["src_files"]
    else:
        src_files = make_lock_files.call_args.kwargs["src_files"]

    assert [p.resolve() for p in src_files] == [
        Path(update_environment.parent / "environment-preupdate.yml")
    ]


@pytest.fixture
def source_paths(tmp_path: Path) -> Path:
    return clone_test_dir("test-source-paths", tmp_path)


def test_run_lock_relative_source_path(
    monkeypatch: "pytest.MonkeyPatch", source_paths: Path, conda_exe: str
):
    """run_lock() stores and restores lockfile-relative source paths"""
    source_paths.joinpath("lockfile").mkdir()
    monkeypatch.chdir(source_paths)
    environment = Path("sources/environment.yaml")
    lockfile = Path("lockfile/conda-lock.yml")
    run_lock([environment], lockfile_path=lockfile, conda_exe="mamba")
    lock_content = parse_conda_lock_file(lockfile)
    locked_environment = lock_content.metadata.sources[0]
    assert Path(locked_environment) == Path("../sources/environment.yaml")
    make_lock_files = MagicMock()
    monkeypatch.setattr("conda_lock.conda_lock.make_lock_files", make_lock_files)
    run_lock(
        DEFAULT_FILES, lockfile_path=lockfile, conda_exe=conda_exe, update=["pydantic"]
    )
    if sys.version_info < (3, 8):
        # backwards compat
        src_files = make_lock_files.call_args_list[0][1]["src_files"]
    else:
        src_files = make_lock_files.call_args.kwargs["src_files"]
    assert [p.resolve() for p in src_files] == [environment.resolve()]


def test_run_lock_with_pip(
    monkeypatch: "pytest.MonkeyPatch", pip_environment: Path, conda_exe: str
):
    monkeypatch.chdir(pip_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock([pip_environment], conda_exe=conda_exe)


@pytest.fixture
def os_name_marker_environment(tmp_path: Path):
    return clone_test_dir("test-os-name-marker", tmp_path).joinpath("environment.yml")


def test_os_name_marker(
    monkeypatch: pytest.MonkeyPatch, os_name_marker_environment: Path, conda_exe: str
):
    monkeypatch.chdir(os_name_marker_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock([os_name_marker_environment], conda_exe=conda_exe)
    lockfile = parse_conda_lock_file(
        os_name_marker_environment.parent / DEFAULT_LOCKFILE_NAME
    )
    for package in lockfile.package:
        assert package.name != "pywinpty"


def test_run_lock_with_pip_environment_different_names_same_deps(
    monkeypatch: "pytest.MonkeyPatch",
    pip_environment_different_names_same_deps: Path,
    conda_exe: str,
):
    monkeypatch.chdir(pip_environment_different_names_same_deps.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock([pip_environment_different_names_same_deps], conda_exe=conda_exe)


def test_run_lock_regression_gh155(
    monkeypatch: "pytest.MonkeyPatch",
    pip_environment_regression_gh155: Path,
    conda_exe: str,
):
    monkeypatch.chdir(pip_environment_regression_gh155.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock([pip_environment_regression_gh155], conda_exe=conda_exe)


def test_run_lock_uppercase_pip(
    monkeypatch: "pytest.MonkeyPatch",
    env_with_uppercase_pip: Path,
    conda_exe: str,
):
    monkeypatch.chdir(env_with_uppercase_pip.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock([env_with_uppercase_pip], conda_exe=conda_exe)


def test_run_lock_with_local_package(
    monkeypatch: "pytest.MonkeyPatch",
    pip_local_package_environment: Path,
    conda_exe: str,
):
    monkeypatch.chdir(pip_local_package_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    virtual_package_repo = default_virtual_package_repodata()

    with virtual_package_repo:
        lock_spec = make_lock_spec(
            src_files=[pip_local_package_environment],
            virtual_package_repo=virtual_package_repo,
        )
    assert not any(
        p.manager == "pip" for p in lock_spec.dependencies
    ), "conda-lock ignores editable pip deps"


def test_run_lock_with_input_hash_check(
    monkeypatch: "pytest.MonkeyPatch",
    input_hash_zlib_environment: Path,
    conda_exe: str,
    capsys: "pytest.CaptureFixture[str]",
):
    monkeypatch.chdir(input_hash_zlib_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    lockfile = input_hash_zlib_environment.parent / "conda-linux-64.lock"
    if lockfile.exists():
        lockfile.unlink()

    run_lock(
        [input_hash_zlib_environment],
        platforms=["linux-64"],
        conda_exe=conda_exe,
        check_input_hash=True,
    )
    stat = lockfile.stat()
    created = stat.st_mtime_ns

    with open(lockfile) as f:
        previous_hash = extract_input_hash(f.read())
        assert previous_hash is not None
        assert len(previous_hash) == 64

    capsys.readouterr()
    run_lock(
        [input_hash_zlib_environment],
        platforms=["linux-64"],
        conda_exe=conda_exe,
        check_input_hash=True,
    )
    stat = lockfile.stat()
    assert stat.st_mtime_ns == created
    output = capsys.readouterr()
    assert "Spec hash already locked for" in output.err


@pytest.mark.parametrize(
    "package,version,url_pattern",
    [
        ("python", ">=3.6,<3.7", "/python-3.6"),
        ("python", "~3.6", "/python-3.6"),
        ("python", "^2.7", "/python-2.7"),
    ],
)
def test_poetry_version_parsing_constraints(
    package: str, version: str, url_pattern: str, capsys: "pytest.CaptureFixture[str]"
):
    _conda_exe = determine_conda_executable(None, mamba=False, micromamba=False)

    vpr = default_virtual_package_repodata()
    with vpr, capsys.disabled():
        with tempfile.NamedTemporaryFile(dir=".") as tf:
            spec = LockSpecification(
                dependencies=[
                    VersionedDependency(
                        name=package,
                        version=poetry_version_to_conda_version(version) or "",
                        manager="conda",
                        optional=False,
                        category="main",
                        extras=[],
                    )
                ],
                channels=[Channel.from_string("conda-forge")],
                platforms=["linux-64"],
                # NB: this file must exist for relative path resolution to work
                # in create_lockfile_from_spec
                sources=[Path(tf.name)],
                virtual_package_repo=vpr,
            )
            lockfile_contents = create_lockfile_from_spec(
                conda=_conda_exe,
                spec=spec,
                lockfile_path=Path(DEFAULT_LOCKFILE_NAME),
                metadata_yamls=(),
            )

        python = next(p for p in lockfile_contents.package if p.name == "python")
        assert url_pattern in python.url


def test_run_with_channel_inversion(
    monkeypatch: "pytest.MonkeyPatch", channel_inversion: Path, mamba_exe: str
):
    """Given that the cuda_python package is available from a few channels
    and three of those channels listed
    and with conda-forge listed as the lowest priority channel
    and with the cuda_python dependency listed as "conda-forge::cuda_python",
    ensure that the lock file parse picks up conda-forge as the channel and not one of the higher priority channels
    """
    monkeypatch.chdir(channel_inversion.parent)
    run_lock([channel_inversion], conda_exe=mamba_exe, platforms=["linux-64"])
    lockfile = parse_conda_lock_file(channel_inversion.parent / DEFAULT_LOCKFILE_NAME)
    for package in lockfile.package:
        if package.name == "cuda-python":
            ms = MatchSpec(package.url)  # type: ignore
            assert ms.get("channel") == "conda-forge"
            break
    else:
        raise ValueError("cuda-python not found!")


def _make_spec(name: str, constraint: str = "*"):
    return VersionedDependency(
        name=name,
        version=constraint,
    )


def _make_dependency_with_platforms(
    name: str, platforms: typing.List[str], constraint: str = "*"
):
    return VersionedDependency(
        name=name,
        version=constraint,
        selectors=Selectors(platform=platforms),
    )


def test_aggregate_lock_specs():
    """Ensure that the way two specs combine when both specify channels is correct"""
    base_spec = LockSpecification(
        dependencies=[_make_spec("python", "=3.7")],
        channels=[Channel.from_string("conda-forge")],
        platforms=["linux-64"],
        sources=[Path("base-env.yml")],
    )

    gpu_spec = LockSpecification(
        dependencies=[_make_spec("pytorch")],
        channels=[Channel.from_string("pytorch"), Channel.from_string("conda-forge")],
        platforms=["linux-64"],
        sources=[Path("ml-stuff.yml")],
    )

    # NB: content hash explicitly does not depend on the source file names
    actual = aggregate_lock_specs([base_spec, gpu_spec])
    expected = LockSpecification(
        dependencies=[
            _make_spec("python", "=3.7"),
            _make_spec("pytorch"),
        ],
        channels=[
            Channel.from_string("pytorch"),
            Channel.from_string("conda-forge"),
        ],
        platforms=["linux-64"],
        sources=[],
    )
    assert actual.dict(exclude={"sources"}) == expected.dict(exclude={"sources"})
    assert actual.content_hash() == expected.content_hash()


def test_aggregate_lock_specs_multiple_platforms():
    """Ensure that plaforms are merged correctly"""
    linux_spec = LockSpecification(
        dependencies=[_make_dependency_with_platforms("python", ["linux-64"], "=3.7")],
        channels=[Channel.from_string("conda-forge")],
        platforms=["linux-64"],
        sources=[Path("base-env.yml")],
    )

    osx_spec = LockSpecification(
        dependencies=[_make_dependency_with_platforms("python", ["osx-64"], "=3.7")],
        channels=[Channel.from_string("conda-forge")],
        platforms=["osx-64"],
        sources=[Path("base-env.yml")],
    )

    # NB: content hash explicitly does not depend on the source file names
    actual = aggregate_lock_specs([linux_spec, osx_spec])
    expected = LockSpecification(
        dependencies=[
            _make_dependency_with_platforms("python", ["linux-64", "osx-64"], "=3.7")
        ],
        channels=[
            Channel.from_string("conda-forge"),
        ],
        platforms=["linux-64", "osx-64"],
        sources=[],
    )
    assert actual.dict(exclude={"sources"}) == expected.dict(exclude={"sources"})
    assert actual.content_hash() == expected.content_hash()


def test_aggregate_lock_specs_override_version():
    base_spec = LockSpecification(
        dependencies=[_make_spec("package", "=1.0")],
        channels=[Channel.from_string("conda-forge")],
        platforms=["linux-64"],
        sources=[Path("base.yml")],
    )

    override_spec = LockSpecification(
        dependencies=[_make_spec("package", "=2.0")],
        channels=[Channel.from_string("internal"), Channel.from_string("conda-forge")],
        platforms=["linux-64"],
        sources=[Path("override.yml")],
    )

    agg_spec = aggregate_lock_specs([base_spec, override_spec])

    assert agg_spec.dependencies == override_spec.dependencies


def test_aggregate_lock_specs_invalid_channels():
    """Ensure that aggregating specs from mismatched channel orderings raises an error."""
    base_spec = LockSpecification(
        dependencies=[],
        channels=[Channel.from_string("defaults")],
        platforms=[],
        sources=[],
    )

    add_conda_forge = base_spec.copy(
        update={
            "channels": [
                Channel.from_string("conda-forge"),
                Channel.from_string("defaults"),
            ]
        }
    )
    agg_spec = aggregate_lock_specs([base_spec, add_conda_forge])
    assert agg_spec.channels == add_conda_forge.channels

    # swap the order of the two channels, which is an error
    flipped = base_spec.copy(
        update={
            "channels": [
                Channel.from_string("defaults"),
                Channel.from_string("conda-forge"),
            ]
        }
    )

    with pytest.raises(ChannelAggregationError):
        agg_spec = aggregate_lock_specs([base_spec, add_conda_forge, flipped])

    add_pytorch = base_spec.copy(
        update={
            "channels": [
                Channel.from_string("pytorch"),
                Channel.from_string("defaults"),
            ]
        }
    )
    with pytest.raises(ChannelAggregationError):
        agg_spec = aggregate_lock_specs([base_spec, add_conda_forge, add_pytorch])


@pytest.fixture(scope="session")
def mamba_exe():
    """Provides a fixture for tests that require mamba"""
    kwargs = dict(
        mamba=True,
        micromamba=False,
        conda=False,
        conda_exe=False,
    )
    _conda_exe = _ensureconda(**kwargs)
    if _conda_exe is not None:
        return _conda_exe
    pytest.skip("mamba is not installed")


def _check_package_installed(package: str, prefix: str):
    import glob

    files = list(glob.glob(f"{prefix}/conda-meta/{package}-*.json"))
    assert len(files) >= 1
    # TODO: validate that all the files are in there
    for fn in files:
        data = json.load(open(fn))
        for expected_file in data["files"]:
            assert (Path(prefix) / Path(expected_file)).exists()
    return True


def conda_supports_env(conda_exe: str):
    try:
        subprocess.check_call(
            [conda_exe, "env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError:
        return False
    return True


@pytest.mark.parametrize("kind", ["explicit", "env"])
@flaky
def test_install(
    request: "pytest.FixtureRequest",
    kind: str,
    tmp_path: Path,
    conda_exe: str,
    zlib_environment: Path,
    monkeypatch: "pytest.MonkeyPatch",
    capsys: "pytest.CaptureFixture[str]",
):
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    if kind == "env" and not conda_supports_env(conda_exe):
        pytest.skip(
            f"Standalone conda @ '{conda_exe}' does not support materializing from environment files."
        )

    package = "zlib"
    platform = "linux-64"

    lock_filename_template = (
        request.node.name + "conda-{platform}-{dev-dependencies}.lock"
    )
    lock_filename = (
        request.node.name
        + "conda-linux-64-true.lock"
        + (".yml" if kind == "env" else "")
    )
    try:
        os.remove(lock_filename)
    except OSError:
        pass

    from click.testing import CliRunner

    with capsys.disabled():
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(
            main,
            [
                "lock",
                "--conda",
                conda_exe,
                "-p",
                platform,
                "-f",
                str(zlib_environment),
                "-k",
                kind,
                "--filename-template",
                lock_filename_template,
            ],
            catch_exceptions=False,
        )
    print(result.stdout, file=sys.stdout)
    print(result.stderr, file=sys.stderr)
    assert result.exit_code == 0

    env_name = "test_env"

    def invoke_install(*extra_args: str):
        with capsys.disabled():
            return runner.invoke(
                main,
                [
                    "install",
                    "--conda",
                    conda_exe,
                    "--prefix",
                    str(tmp_path / env_name),
                    *extra_args,
                    lock_filename,
                ],
                catch_exceptions=False,
            )

    if sys.platform.lower().startswith("linux"):
        context = contextlib.nullcontext()
    else:
        # since by default we do platform validation we would expect this to fail
        context = pytest.raises(PlatformValidationError)

    with context, install_lock():
        result = invoke_install()
    print(result.stdout, file=sys.stdout)
    print(result.stderr, file=sys.stderr)
    if Path(lock_filename).exists():
        logging.debug(
            "lockfile contents: \n\n=======\n%s\n\n==========",
            Path(lock_filename).read_text(),
        )

    if sys.platform.lower().startswith("linux"):
        assert _check_package_installed(
            package=package,
            prefix=str(tmp_path / env_name),
        ), f"Package {package} does not exist in {tmp_path} environment"


@pytest.mark.parametrize(
    "line,stripped",
    (
        (
            "https://conda.mychannel.cloud/mypackage",
            "https://conda.mychannel.cloud/mypackage",
        ),
        (
            "https://user:password@conda.mychannel.cloud/mypackage",
            "https://conda.mychannel.cloud/mypackage",
        ),
        (
            "http://conda.mychannel.cloud/mypackage",
            "http://conda.mychannel.cloud/mypackage",
        ),
        (
            "http://user:password@conda.mychannel.cloud/mypackage",
            "http://conda.mychannel.cloud/mypackage",
        ),
        (
            "# pip mypackage @ https://username1:password1@pypi.mychannel.cloud/simple",
            "# pip mypackage @ https://pypi.mychannel.cloud/simple",
        ),
        (
            "# pip mypackage @ https://pypi.mychannel.cloud/simple",
            "# pip mypackage @ https://pypi.mychannel.cloud/simple",
        ),
    ),
)
def test__strip_auth_from_line(line: str, stripped: str):
    assert _strip_auth_from_line(line) == stripped


@pytest.mark.parametrize(
    "line,stripped",
    (
        ("https://conda.mychannel.cloud/mypackage", "conda.mychannel.cloud"),
        ("http://conda.mychannel.cloud/mypackage", "conda.mychannel.cloud"),
        (
            "# pip mypackage @ https://pypi.mychannel.cloud/simple",
            "pypi.mychannel.cloud",
        ),
    ),
)
def test__extract_domain(line: str, stripped: str):
    assert _extract_domain(line) == stripped


def _read_file(filepath: "str | Path") -> str:
    with open(filepath, mode="r") as file_pointer:
        return file_pointer.read()


@pytest.mark.parametrize(
    "lockfile,stripped_lockfile",
    tuple(
        (
            _read_file(
                Path(__file__)
                .parent.joinpath("test-lockfile")
                .joinpath(f"{filename}.lock")
            ),
            _read_file(
                Path(__file__)
                .parent.joinpath("test-stripped-lockfile")
                .joinpath(f"{filename}.lock")
            ),
        )
        for filename in ("test", "no-auth")
    ),
)
def test__strip_auth_from_lockfile(lockfile: str, stripped_lockfile: str):
    assert _strip_auth_from_lockfile(lockfile) == stripped_lockfile


@pytest.mark.parametrize(
    "line,auth,line_with_auth",
    (
        (
            "https://conda.mychannel.cloud/mypackage",
            {"conda.mychannel.cloud": "username:password"},
            "https://username:password@conda.mychannel.cloud/mypackage",
        ),
        (
            "https://conda.mychannel.cloud/mypackage",
            {},
            "https://conda.mychannel.cloud/mypackage",
        ),
        (
            "https://conda.mychannel.cloud/channel1/mypackage",
            {"conda.mychannel.cloud/channel1": "username:password"},
            "https://username:password@conda.mychannel.cloud/channel1/mypackage",
        ),
        (
            "https://conda.mychannel.cloud/channel1/mypackage",
            {
                "conda.mychannel.cloud": "username:password",
                "conda.mychannel.cloud/channel1": "username1:password1",
            },
            "https://username1:password1@conda.mychannel.cloud/channel1/mypackage",
        ),
        (
            "# pip mypackage @ https://pypi.mychannel.cloud/simple",
            {
                "pypi.mychannel.cloud": "username:password",
                "pypi.mychannel.cloud/simple": "username1:password1",
            },
            "# pip mypackage @ https://username1:password1@pypi.mychannel.cloud/simple",
        ),
        (
            "# pip mypackage @ https://pypi.otherchannel.cloud/simple",
            {
                "pypi.mychannel.cloud": "username:password",
                "pypi.mychannel.cloud/simple": "username1:password1",
            },
            "# pip mypackage @ https://pypi.otherchannel.cloud/simple",
        ),
    ),
)
def test__add_auth_to_line(line: str, auth: Dict[str, str], line_with_auth: str):
    assert _add_auth_to_line(line, auth) == line_with_auth


@pytest.fixture(name="auth")
def auth_():
    return {
        "a.mychannel.cloud": "username_a:password_a",
        "c.mychannel.cloud": "username_c:password_c",
        "d.mychannel.cloud": "username_d:password_d",
    }


@pytest.mark.parametrize(
    "stripped_lockfile,lockfile_with_auth",
    tuple(
        (
            _read_file(TEST_DIR / "test-stripped-lockfile" / f"{filename}.lock"),
            _read_file(TEST_DIR / "test-lockfile-with-auth" / f"{filename}.lock"),
        )
        for filename in ("test",)
    ),
)
def test__add_auth_to_lockfile(
    stripped_lockfile: str, lockfile_with_auth: str, auth: Dict[str, str]
):
    assert _add_auth_to_lockfile(stripped_lockfile, auth) == lockfile_with_auth


@pytest.mark.parametrize("kind", ["explicit", "env"])
def test_virtual_packages(
    conda_exe: str,
    monkeypatch: "pytest.MonkeyPatch",
    kind: str,
    capsys: "pytest.CaptureFixture[str]",
):
    test_dir = TEST_DIR.joinpath("test-cuda")
    monkeypatch.chdir(test_dir)

    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    if kind == "env" and not conda_supports_env(conda_exe):
        pytest.skip(
            f"Standalone conda @ '{conda_exe}' does not support materializing from environment files."
        )

    platform = "linux-64"

    from click.testing import CliRunner

    for lockfile in glob(f"conda-{platform}.*"):
        os.unlink(lockfile)

    with capsys.disabled():
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(
            main,
            [
                "lock",
                "--conda",
                conda_exe,
                "-p",
                platform,
                "-k",
                kind,
            ],
        )

    print(result.stdout, file=sys.stdout)
    print(result.stderr, file=sys.stderr)
    if result.exception:
        raise result.exception
    assert result.exit_code == 0

    for lockfile in glob(f"conda-{platform}.*"):
        os.unlink(lockfile)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        main,
        [
            "lock",
            "--conda",
            conda_exe,
            "-p",
            platform,
            "-k",
            kind,
            "--virtual-package-spec",
            str(test_dir / "virtual-packages-old-glibc.yaml"),
        ],
    )

    # micromamba doesn't respect the CONDA_OVERRIDE_XXX="" env vars appropriately so it will include the
    # system virtual packages regardless of whether they should be present.  Skip this check in that case
    if not is_micromamba(conda_exe):
        assert result.exit_code != 0


def test_virtual_package_input_hash_stability():
    from conda_lock.virtual_package import virtual_package_repo_from_specification

    test_dir = TEST_DIR.joinpath("test-cuda")
    vspec = test_dir / "virtual-packages-old-glibc.yaml"

    vpr = virtual_package_repo_from_specification(vspec)
    spec = LockSpecification(
        dependencies=[],
        channels=[],
        platforms=["linux-64"],
        sources=[],
        virtual_package_repo=vpr,
    )
    expected = "8ee5fc79fca4cb7732d2e88443209e0a3a354da9899cb8899d94f9b1dcccf975"
    assert spec.content_hash() == {"linux-64": expected}


def test_default_virtual_package_input_hash_stability():
    from conda_lock.virtual_package import default_virtual_package_repodata

    vpr = default_virtual_package_repodata()

    expected = {
        "linux-64": "dbd71bccc4b3be81038e44b1f14891ccec40a6d70a43cfe02295fc77a2ea9eb5",
        "linux-aarch64": "023611fb84c00fb5aaeddde1e0ac62e6ae007aecf6f69ccb5dbfc3cd6d945436",
        "linux-ppc64le": "8533a7bd0e950f7b085eeef7686d2c4895e84b8ffdbfba6d62863072ac41090c",
        "osx-64": "b7eebe4be0654740f67e3023f2ede298f390119ef225f50ad7e7288ea22d5c93",
        "osx-arm64": "cc82018d1b1809b9aebacacc5ed05ee6a4318b3eba039607d2a6957571f8bf2b",
        "win-64": "44239e9f0175404e62e4a80bb8f4be72e38c536280d6d5e484e52fa04b45c9f6",
    }

    spec = LockSpecification(
        dependencies=[],
        channels=[],
        platforms=list(expected.keys()),
        sources=[],
        virtual_package_repo=vpr,
    )
    assert spec.content_hash() == expected


@pytest.fixture
def conda_lock_yaml():
    return (
        Path(__file__).parent.joinpath("test-lockfile").joinpath(DEFAULT_LOCKFILE_NAME)
    )


def test_fake_conda_env(conda_exe: str, conda_lock_yaml: Path):
    lockfile_content = parse_conda_lock_file(conda_lock_yaml)

    with fake_conda_environment(
        lockfile_content.package, platform="linux-64"
    ) as prefix:
        subprocess.call(
            [
                conda_exe,
                "list",
                "--debug",
                "-p",
                prefix,
                "--json",
            ]
        )
        packages = json.loads(
            subprocess.check_output(
                [
                    conda_exe,
                    "list",
                    "--debug",
                    "-p",
                    prefix,
                    "--json",
                ]
            )
        )
        locked = {
            p.name: p
            for p in lockfile_content.package
            if p.manager == "conda" and p.platform == "linux-64"
        }
        assert len(packages) == len(locked)
        for env_package in packages:
            locked_package = locked[env_package["name"]]

            platform = env_package["platform"]
            path = pathlib.PurePosixPath(
                urlsplit(urldefrag(locked_package.url)[0]).path
            )
            expected_channel = "conda-forge"
            expected_base_url = "https://conda.anaconda.org/conda-forge"
            if is_micromamba(conda_exe):
                assert env_package["base_url"] in {
                    f"{expected_base_url}/{platform}",
                    expected_base_url,
                }
                assert env_package["channel"] in {
                    f"{expected_channel}/{platform}",
                    expected_channel,
                }
            else:
                assert env_package["base_url"] == expected_base_url
                assert env_package["channel"] == expected_channel
            assert env_package["dist_name"] == f"{path.name[:-8]}"
            assert platform == path.parent.name


@pytest.mark.parametrize("placeholder", ["$QUETZ_API_KEY", "${QUETZ_API_KEY}"])
@flaky
def test_private_lock(
    quetz_server: "QuetzServerInfo",
    tmp_path: Path,
    monkeypatch: "pytest.MonkeyPatch",
    capsys: "pytest.CaptureFixture[str]",
    conda_exe: str,
    placeholder,
):
    if is_micromamba(conda_exe):
        res = subprocess.run(
            [conda_exe, "--version"], stdout=subprocess.PIPE, encoding="utf8"
        )
        logging.info("using micromamba version %s", res.stdout)
        pytest.xfail("micromamba doesn't support our quetz server urls properly")
    from ensureconda.resolve import platform_subdir

    monkeypatch.setenv("QUETZ_API_KEY", quetz_server.api_key)
    monkeypatch.chdir(tmp_path)

    content = yaml.safe_dump(
        {
            "channels": [f"{quetz_server.url}/t/{placeholder}/get/proxy-channel"],
            "platforms": [platform_subdir()],
            "dependencies": ["zlib"],
        }
    )
    (tmp_path / "environment.yml").write_text(content)

    with capsys.disabled():
        from click.testing import CliRunner, Result

        runner = CliRunner(mix_stderr=False)
        result: Result = runner.invoke(
            main,
            [
                "lock",
                "--conda",
                conda_exe,
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    def run_install():
        with capsys.disabled():
            runner = CliRunner(mix_stderr=False)
            env_name = uuid.uuid4().hex
            env_prefix = tmp_path / env_name

            with install_lock():
                result: Result = runner.invoke(
                    main,
                    [
                        "install",
                        "--conda",
                        conda_exe,
                        "--prefix",
                        str(env_prefix),
                        str(tmp_path / "conda-lock.yml"),
                    ],
                    catch_exceptions=False,
                )

        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
        assert result.exit_code == 0

    run_install()

    monkeypatch.delenv("QUETZ_API_KEY")
    with pytest.raises(MissingEnvVarError):
        run_install()


def test_extract_json_object():
    """It should remove all the characters after the last }"""
    assert extract_json_object(' ^[0m {"key1": true } ^[0m') == '{"key1": true }'
    assert extract_json_object('{"key1": true }') == '{"key1": true }'


def test_cli_version(capsys: "pytest.CaptureFixture[str]"):
    """It should correctly report its version."""

    from click.testing import CliRunner

    with capsys.disabled():
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(
            main,
            [
                "--version",
            ],
            catch_exceptions=False,
        )
    print(result.stdout, file=sys.stdout)
    print(result.stderr, file=sys.stderr)
    assert result.exit_code == 0
    # Sometimes __version__ looks like "0.11.3.dev370+g315f170" and the part after
    # ".dev" is often out-of-sync when doing local development. So we check only for
    # the part before, in this case just "0.11.3".
    version_without_dev = __version__.split(".dev")[0]
    assert version_without_dev in result.stdout
