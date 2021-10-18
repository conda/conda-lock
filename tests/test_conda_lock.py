import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys

from glob import glob
from typing import Any, MutableSequence
from urllib.parse import urldefrag, urlsplit

import pytest

from pytest_mock import MockerFixture

from conda_lock.conda_lock import (
    DEFAULT_PLATFORMS,
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
    default_virtual_package_repodata,
    determine_conda_executable,
    fake_conda_environment,
    is_micromamba,
    main,
    make_lock_specs,
    parse_meta_yaml_file,
    run_lock,
    solve_specs_for_arch,
)
from conda_lock.pypi_solver import parse_pip_requirement, solve_pypi
from conda_lock.src_parser import LockSpecification
from conda_lock.src_parser.environment_yaml import parse_environment_file
from conda_lock.src_parser.explicit import parse_explicit_file
from conda_lock.src_parser.pyproject_toml import (
    parse_flit_pyproject_toml,
    parse_poetry_pyproject_toml,
    poetry_version_to_conda_version,
    to_match_spec,
)


TEST_DIR = pathlib.Path(__file__).parent


@pytest.fixture(autouse=True)
def logging_setup(caplog):
    caplog.set_level(logging.DEBUG)


@pytest.fixture
def gdal_environment():
    return TEST_DIR.joinpath("gdal").joinpath("environment.yml")


@pytest.fixture
def pip_environment():
    return TEST_DIR.joinpath("test-pypi-resolve").joinpath("environment.yml")


@pytest.fixture
def zlib_environment():
    return TEST_DIR.joinpath("zlib").joinpath("environment.yml")


@pytest.fixture
def input_hash_zlib_environment():
    return (
        pathlib.Path(__file__)
        .parent.joinpath("test-input-hash-zlib")
        .joinpath("environment.yml")
    )


@pytest.fixture
def meta_yaml_environment():
    return TEST_DIR.joinpath("test-recipe").joinpath("meta.yaml")


@pytest.fixture
def poetry_pyproject_toml():
    return TEST_DIR.joinpath("test-poetry").joinpath("pyproject.toml")


@pytest.fixture
def flit_pyproject_toml():
    return TEST_DIR.joinpath("test-flit").joinpath("pyproject.toml")


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


def test_parse_environment_file_with_pip(pip_environment):
    res = parse_environment_file(pip_environment, "linux-64")
    assert res.pip_specs == ["requests-toolbelt==0.9.1"]


def test_choose_wheel() -> None:

    solution = solve_pypi(
        ["fastavro"],
        use_latest=[],
        pip_locked=[],
        conda_locked=[],
        python_version="3.9.7",
        platform="linux-64",
    )
    assert len(solution) == 1
    assert solution[0]["hashes"] == [
        "sha256:fafe37983605ed74a5ca8063951f6d5984ad871e0ff895f14afa81a6d88c316e"
    ]


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
def test_parse_pip_requirement(requirement, parsed):
    assert parse_pip_requirement(requirement) == parsed


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
    assert "tomlkit[version='>=0.7.0,<1.0.0']" not in res.specs

    res = parse_poetry_pyproject_toml(
        poetry_pyproject_toml,
        platform="linux-64",
        include_dev_dependencies=include_dev_dependencies,
        extras={"tomlkit"},
    )

    assert "tomlkit[version='>=0.7.0,<1.0.0']" in res.specs


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


def test_run_lock_with_pip(monkeypatch, pip_environment, conda_exe):
    monkeypatch.chdir(pip_environment.parent)
    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    run_lock([pip_environment], conda_exe=conda_exe)


def test_platforms_from_pyproject(monkeypatch, mocker: MockerFixture):
    pyproject = TEST_DIR.joinpath("test-platforms").joinpath("pyproject.toml")
    monkeypatch.chdir(pyproject.parent)
    mock = mocker.patch("conda_lock.conda_lock.make_lock_files")
    run_lock([pyproject], conda_exe=None)
    assert sorted(mock.call_args.kwargs["platforms"]) == ["none", "such"]

    pyproject = TEST_DIR.joinpath("test-poetry").joinpath("pyproject.toml")
    monkeypatch.chdir(pyproject.parent)
    run_lock([pyproject], conda_exe=None)
    assert sorted(mock.call_args.kwargs["platforms"]) == sorted(DEFAULT_PLATFORMS)


def test_solve_with_pip(pip_environment, conda_exe):

    virtual_package_repo = default_virtual_package_repodata()

    with virtual_package_repo:
        lock_specs = make_lock_specs(
            platforms=["linux-64"],
            src_files=[pip_environment],
            include_dev_dependencies=False,
            channel_overrides=None,
            extras=None,
            virtual_package_repo=virtual_package_repo,
        )

        spec = lock_specs["linux-64"]

        dry_run_install = solve_specs_for_arch(
            conda=conda_exe,
            platform=spec.platform,
            channels=[*spec.channels, virtual_package_repo.channel_url],
            specs=spec.specs,
        )

    python_version = None
    locked_packages = []
    for package in (
        dry_run_install["actions"]["FETCH"] + dry_run_install["actions"]["LINK"]
    ):
        if package["name"] == "python":
            python_version = package["version"]
        else:
            locked_packages.append((package["name"], package["version"]))
    assert python_version.startswith("3.9.")

    pip_installs = solve_pypi(
        spec.pip_specs,
        use_latest=[],
        pip_locked=[],
        conda_locked=locked_packages,
        python_version=python_version,
        platform="linux-64",
    )
    assert len(pip_installs) == 1
    assert pip_installs[0]["name"] == "requests-toolbelt"
    assert pip_installs[0]["version"] == "0.9.1"

    pip_installs = solve_pypi(
        [
            "requests-toolbelt @ https://files.pythonhosted.org/packages/60/ef/7681134338fc097acef8d9b2f8abe0458e4d87559c689a8c306d0957ece5/requests_toolbelt-0.9.1-py2.py3-none-any.whl#sha256=380606e1d10dc85c3bd47bf5a6095f815ec007be7a8b69c878507068df059e6f"
        ],
        use_latest=[],
        conda_locked=locked_packages,
        pip_locked=[],
        python_version=python_version,
        platform="linux-64",
    )
    assert len(pip_installs) == 1
    assert pip_installs[0]["name"] == "requests-toolbelt"
    assert pip_installs[0].get("version") is None
    assert (
        pip_installs[0]["url"]
        == "https://files.pythonhosted.org/packages/60/ef/7681134338fc097acef8d9b2f8abe0458e4d87559c689a8c306d0957ece5/requests_toolbelt-0.9.1-py2.py3-none-any.whl"
    )
    assert pip_installs[0]["hashes"] == [
        "sha256:380606e1d10dc85c3bd47bf5a6095f815ec007be7a8b69c878507068df059e6f"
    ]


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
    from conda_lock.virtual_package import default_virtual_package_repodata

    vpr = default_virtual_package_repodata()
    with vpr:
        spec = LockSpecification(
            specs=[to_match_spec(package, poetry_version_to_conda_version(version))],
            channels=["conda-forge"],
            platform="linux-64",
            virtual_package_repo=vpr,
        )
        lockfile_contents = create_lockfile_from_spec(
            conda=_conda_exe,
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
        # pytest.param("conda_exe"),
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
            _read_file(TEST_DIR / "test-stripped-lockfile" / f"{filename}.lock"),
            _read_file(TEST_DIR / "test-lockfile-with-auth" / f"{filename}.lock"),
        )
        for filename in ("test",)
    ),
)
def test__add_auth_to_lockfile(stripped_lockfile, lockfile_with_auth, auth):
    assert _add_auth_to_lockfile(stripped_lockfile, auth) == lockfile_with_auth


@pytest.mark.parametrize("kind", ["explicit", "env"])
def test_virtual_packages(conda_exe, monkeypatch, kind):
    test_dir = TEST_DIR.joinpath("test-cuda")
    monkeypatch.chdir(test_dir)

    if is_micromamba(conda_exe):
        monkeypatch.setenv("CONDA_FLAGS", "-v")
    if kind == "env" and not conda_supports_env(conda_exe):
        pytest.skip(
            f"Standalone conda @ '{conda_exe}' does not support materializing from environment files."
        )

    platform = "linux-64"

    from click.testing import CliRunner, Result

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
        ],
    )

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
            test_dir / "virtual-packages-old-glibc.yaml",
        ],
    )

    # micromamba doesn't respect the CONDA_OVERRIDE_XXX="" env vars appropriately so it will include the
    # system virtual packages regardless of whether they should be present.  Skip this check in that case
    if not is_micromamba(conda_exe):
        assert result.exit_code != 0


def test_virtual_package_input_hash_stability():
    from conda_lock.virtual_package import virtual_package_repo_from_specification

    test_dir = TEST_DIR.joinpath("test-cuda")
    spec = test_dir / "virtual-packages-old-glibc.yaml"

    vpr = virtual_package_repo_from_specification(spec)
    spec = LockSpecification([], [], "linux-64", virtual_package_repo=vpr)
    expected = "dd3db10126e00cd63c1fa7713f4a1f9831f6f44fabd0f5d79ac906820a7f4917"
    assert spec.input_hash() == expected


def _param(platform, hash):
    return pytest.param(platform, hash, id=platform)


@pytest.mark.parametrize(
    ["platform", "expected"],
    [
        # fmt: off
        _param("linux-64", "fae755df14d75217a7e2bee4ed783a4ee78fbdbcf6d116bbe6219111c522faae"),
        _param("linux-aarch64", "4e36c02f9a51bd81b01f8ff1a573d76dc35f624341a8bd1c8d89e3f24eafdee9"),
        _param("linux-ppc64le", "4a59cae31ce96a0ed3cc0919531aa2c39deb75af581992b4381803506916cac2"),
        _param("osx-64", "fb3daef6cf7780d4d7a64a36decc095f01778f5e8a4b575f6ef54c7a9b0fbbdf"),
        _param("osx-arm64", "4df1c8002040537b0d5132fe59067325a32e0bef0f8429c5b92914effc161804"),
        _param("win-64", "b98d3b765676e05e7bfe76d3ee1de58f2dd2abbdb033b463bf6e3b0d4cd0a91f"),
        # fmt: on
    ],
)
def test_default_virtual_package_input_hash_stability(platform, expected):
    from conda_lock.virtual_package import default_virtual_package_repodata

    vpr = default_virtual_package_repodata()
    spec = LockSpecification([], [], platform, virtual_package_repo=vpr)
    assert spec.input_hash() == expected


@pytest.fixture
def explicit_lockfile():
    return (
        pathlib.Path(__file__)
        .parent.joinpath("test-lockfile")
        .joinpath("explicit.lock")
    )


# @pytest.fixture
# def explicit_lockfile():
#     return (
#         pathlib.Path(__file__)
#         .parent.joinpath("zlib")
#         .joinpath("conda-osx-64.lock")
#     )


def test_parse_explicit_lockfile(explicit_lockfile):
    conda, pip = parse_explicit_file(explicit_lockfile)

    assert {
        "name": "pymage",
        "version": None,
        "url": "https://github.com/MickaelRigault/pymage/archive/v1.0.tar.gz",
        "hashes": [
            "sha256=11e99c4ea06b76ca7fb5b42d1d35d64139a4fa6f7f163a2f0f9cc3ea0b3c55eb"
        ],
    } in pip
    assert {
        "name": "aiohttp",
        "version": "3.7.4.post0",
        "url": "https://files.pythonhosted.org/packages/34/40/2b3295eb6f66209d1b2ad6ef34ebdf1cb1675c62f8697a8fe7c4f11d2838/aiohttp-3.7.4.post0-cp39-cp39-manylinux2014_x86_64.whl",
        "hashes": [
            "sha256=17c073de315745a1510393a96e680d20af8e67e324f70b42accbd4cb3315c9fb"
        ],
    } in pip

    assert (
        "https://conda.anaconda.org/conda-forge/linux-64/python-3.9.0-hffdb5ce_5_cpython.tar.bz2#d26d64e4cf67cbfab3caf9176c9255de"
        in conda
    )


def test_fake_conda_env(conda_exe, explicit_lockfile):

    specs, pip_specs = parse_explicit_file(explicit_lockfile)
    with open(explicit_lockfile) as f:
        urls = [line.strip() for line in f if line.startswith("http")]

    with fake_conda_environment(urls) as prefix:
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
        assert len(packages) == len(urls)
        url_for_name = {
            "-".join(pathlib.Path(urlsplit(u).path).name.split("-")[:-2]): u
            for u in urls
        }
        for p, u in zip(packages, urls):
            u = url_for_name[p["name"]]
            path = pathlib.Path(urlsplit(urldefrag(u)[0]).path)
            platform = p["platform"]
            if is_micromamba(conda_exe):
                assert (
                    p["base_url"]
                    == f"https://conda.anaconda.org/conda-forge/{platform}"
                )
                assert p["channel"] == f"conda-forge/{platform}"
            else:
                assert p["base_url"] == "https://conda.anaconda.org/conda-forge"
                assert p["channel"] == "conda-forge"
            assert p["dist_name"] == f"{path.name[:-8]}"
            assert platform == path.parent.name
