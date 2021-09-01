import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys

from typing import Any, MutableSequence

import pytest

from conda_lock.conda_lock import (
    PathLike,
    _add_auth_to_line,
    _add_auth_to_lockfile,
    _ensureconda,
    _extract_domain,
    _strip_auth_from_line,
    _strip_auth_from_lockfile,
    aggregate_lock_specs,
    conda_env_override,
    create_lockfile_from_spec,
    determine_conda_executable,
    is_micromamba,
    main,
    parse_meta_yaml_file,
    run_lock,
)
from conda_lock.src_parser import LockSpecification
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.pyproject_toml import (
    parse_flit_pyproject_toml,
    parse_poetry_pyproject_toml,
    poetry_version_to_conda_version,
    to_match_spec,
)


@pytest.fixture(autouse=True)
def logging_setup(caplog):
    caplog.set_level(logging.DEBUG)


@pytest.fixture
def gdal_environment():
    return pathlib.Path(__file__).parent.joinpath("gdal").joinpath("environment.yml")


@pytest.fixture
def zlib_environment():
    return pathlib.Path(__file__).parent.joinpath("zlib").joinpath("environment.yml")


@pytest.fixture
def input_hash_zlib_environment():
    return (
        pathlib.Path(__file__)
        .parent.joinpath("test-input-hash-zlib")
        .joinpath("environment.yml")
    )


@pytest.fixture
def meta_yaml_environment():
    return pathlib.Path(__file__).parent.joinpath("test-recipe").joinpath("meta.yaml")


@pytest.fixture
def poetry_pyproject_toml():
    return (
        pathlib.Path(__file__).parent.joinpath("test-poetry").joinpath("pyproject.toml")
    )


@pytest.fixture
def flit_pyproject_toml():
    return (
        pathlib.Path(__file__).parent.joinpath("test-flit").joinpath("pyproject.toml")
    )


@pytest.fixture(
    scope="function",
    params=[
        pytest.param(True, id="--dev-dependencies"),
        pytest.param(False, id="--no-dev-dependencies"),
    ],
)
def include_dev_dependencies(request: Any) -> bool:
    return request.param


def test_parse_environment_file(gdal_environment):
    res = parse_environment_file(gdal_environment, "linux-64")
    assert all(x in res.specs for x in ["python >=3.7,<3.8", "gdal"])
    assert all(x in res.channels for x in ["conda-forge", "defaults"])


def test_parse_meta_yaml_file(meta_yaml_environment, include_dev_dependencies):
    res = parse_meta_yaml_file(
        meta_yaml_environment,
        platform="linux-64",
        include_dev_dependencies=include_dev_dependencies,
    )
    assert all(x in res.specs for x in ["python", "numpy"])
    # Ensure that this dep specified by a python selector is ignored
    assert "enum34" not in res.specs
    # Ensure that this platform specific dep is included
    assert "zlib" in res.specs
    assert ("pytest" in res.specs) == include_dev_dependencies


def test_parse_poetry(poetry_pyproject_toml, include_dev_dependencies):
    res = parse_poetry_pyproject_toml(
        poetry_pyproject_toml,
        platform="linux-64",
        include_dev_dependencies=include_dev_dependencies,
    )

    assert "requests[version='>=2.13.0,<3.0.0']" in res.specs
    assert "toml[version='>=0.10']" in res.specs
    assert "sqlite[version='<3.34']" in res.specs
    assert "certifi[version='>=2019.11.28']" in res.specs
    assert ("pytest[version='>=5.1.0,<5.2.0']" in res.specs) == include_dev_dependencies
    assert res.channels == ["defaults"]


def test_parse_flit(flit_pyproject_toml, include_dev_dependencies):
    res = parse_flit_pyproject_toml(
        flit_pyproject_toml,
        platform="linux-64",
        include_dev_dependencies=include_dev_dependencies,
    )

    assert "requests[version='>=2.13.0']" in res.specs
    assert "toml[version='>=0.10']" in res.specs
    assert "sqlite[version='<3.34']" in res.specs
    assert "certifi[version='>=2019.11.28']" in res.specs
    # test deps
    assert ("pytest[version='>=5.1.0']" in res.specs) == include_dev_dependencies
    assert res.channels == ["defaults"]


def test_run_lock(monkeypatch, zlib_environment, conda_exe):
    monkeypatch.chdir(zlib_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock([zlib_environment], conda_exe=conda_exe)


def test_run_lock_with_input_hash_check(
    monkeypatch, input_hash_zlib_environment: pathlib.Path, conda_exe, capsys
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
def test_poetry_version_parsing_constraints(package, version, url_pattern):
    _conda_exe = determine_conda_executable("conda", mamba=False, micromamba=False)
    spec = LockSpecification(
        specs=[to_match_spec(package, poetry_version_to_conda_version(version))],
        channels=["conda-forge"],
        platform="linux-64",
    )
    lockfile_contents = create_lockfile_from_spec(
        conda=_conda_exe,
        channels=spec.channels,
        spec=spec,
        kind="explicit",
    )

    for line in lockfile_contents:
        if url_pattern in line:
            break
    else:
        raise ValueError(f"could not find {package} {version}")


def test_aggregate_lock_specs():
    gpu_spec = LockSpecification(
        specs=["pytorch"],
        channels=["pytorch", "conda-forge"],
        platform="linux-64",
    )

    base_spec = LockSpecification(
        specs=["python =3.7"],
        channels=["conda-forge"],
        platform="linux-64",
    )

    assert (
        aggregate_lock_specs([gpu_spec, base_spec]).input_hash()
        == LockSpecification(
            specs=["pytorch", "python =3.7"],
            channels=["pytorch", "conda-forge"],
            platform="linux-64",
        ).input_hash()
    )

    assert (
        aggregate_lock_specs([base_spec, gpu_spec]).input_hash()
        == LockSpecification(
            specs=["pytorch", "python =3.7"],
            channels=["conda-forge"],
            platform="linux-64",
        ).input_hash()
    )


@pytest.fixture(
    scope="session",
    params=[
        pytest.param("conda"),
        pytest.param("mamba"),
        pytest.param("micromamba"),
        pytest.param("conda_exe"),
    ],
)
def conda_exe(request):
    kwargs = dict(
        mamba=False,
        micromamba=False,
        conda=False,
        conda_exe=False,
    )
    kwargs[request.param] = True
    _conda_exe = _ensureconda(**kwargs)

    if _conda_exe is not None:
        return _conda_exe
    raise pytest.skip(f"{request.param} is not installed")


def _check_package_installed(package: str, prefix: str):
    import glob

    files = list(glob.glob(f"{prefix}/conda-meta/{package}-*.json"))
    assert len(files) >= 1
    # TODO: validate that all the files are in there
    for fn in files:
        data = json.load(open(fn))
        for expected_file in data["files"]:
            assert (pathlib.Path(prefix) / pathlib.Path(expected_file)).exists()
    return True


def conda_supports_env(conda_exe):
    try:
        subprocess.check_call(
            [conda_exe, "env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError:
        return False
    return True


@pytest.mark.parametrize("kind", ["explicit", "env"])
def test_install(kind, tmp_path, conda_exe, zlib_environment, monkeypatch):
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    if kind == "env" and not conda_supports_env(conda_exe):
        pytest.skip(
            f"Standalone conda @ '{conda_exe}' does not support materializing from environment files."
        )

    package = "zlib"
    platform = "linux-64"

    lock_filename_template = "conda-{platform}-{dev-dependencies}.lock"
    lock_filename = "conda-linux-64-true.lock" + (".yml" if kind == "env" else "")
    try:
        os.remove(lock_filename)
    except OSError:
        pass

    from click.testing import CliRunner

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
            zlib_environment,
            "-k",
            kind,
            "--filename-template",
            lock_filename_template,
        ],
    )
    if result.exit_code != 0:
        print(result.stdout, file=sys.stdout)
        print(result.stderr, file=sys.stderr)
    assert result.exit_code == 0

    env_name = "test_env"

    def invoke_install(*extra_args):
        return runner.invoke(
            main,
            [
                "install",
                "--conda",
                conda_exe,
                "--prefix",
                tmp_path / env_name,
                *extra_args,
                lock_filename,
            ],
        )

    result = invoke_install()
    print(result.stdout, file=sys.stdout)
    print(result.stderr, file=sys.stderr)
    logging.debug(
        "lockfile contents: \n\n=======\n%s\n\n==========",
        pathlib.Path(lock_filename).read_text(),
    )
    if sys.platform.lower().startswith("linux"):
        assert result.exit_code == 0
        assert _check_package_installed(
            package=package,
            prefix=str(tmp_path / env_name),
        ), f"Package {package} does not exist in {tmp_path} environment"
    else:
        # since by default we do platform validation we would expect this to fail
        assert result.exit_code != 0


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
    ),
)
def test__strip_auth_from_line(line, stripped):
    assert _strip_auth_from_line(line) == stripped


@pytest.mark.parametrize(
    "line,stripped",
    (
        ("https://conda.mychannel.cloud/mypackage", "conda.mychannel.cloud"),
        ("http://conda.mychannel.cloud/mypackage", "conda.mychannel.cloud"),
    ),
)
def test__extract_domain(line, stripped):
    assert _extract_domain(line) == stripped


def _read_file(filepath):
    with open(filepath, mode="r") as file_pointer:
        return file_pointer.read()


@pytest.mark.parametrize(
    "lockfile,stripped_lockfile",
    tuple(
        (
            _read_file(
                pathlib.Path(__file__)
                .parent.joinpath("test-lockfile")
                .joinpath(f"{filename}.lock")
            ),
            _read_file(
                pathlib.Path(__file__)
                .parent.joinpath("test-stripped-lockfile")
                .joinpath(f"{filename}.lock")
            ),
        )
        for filename in ("test", "no-auth")
    ),
)
def test__strip_auth_from_lockfile(lockfile, stripped_lockfile):
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
    ),
)
def test__add_auth_to_line(line, auth, line_with_auth):
    assert _add_auth_to_line(line, auth) == line_with_auth


@pytest.fixture(name="auth")
def auth_():
    return {
        "a.mychannel.cloud": "username_a:password_a",
        "c.mychannel.cloud": "username_c:password_c",
    }


@pytest.mark.parametrize(
    "stripped_lockfile,lockfile_with_auth",
    tuple(
        (
            _read_file(
                pathlib.Path(__file__)
                .parent.joinpath("test-stripped-lockfile")
                .joinpath(f"{filename}.lock")
            ),
            _read_file(
                pathlib.Path(__file__)
                .parent.joinpath("test-lockfile-with-auth")
                .joinpath(f"{filename}.lock")
            ),
        )
        for filename in ("test",)
    ),
)
def test__add_auth_to_lockfile(stripped_lockfile, lockfile_with_auth, auth):
    assert _add_auth_to_lockfile(stripped_lockfile, auth) == lockfile_with_auth
