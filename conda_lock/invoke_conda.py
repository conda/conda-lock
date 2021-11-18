import atexit
import json
import logging
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile

from distutils.version import LooseVersion
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

import ensureconda


PathLike = Union[str, pathlib.Path]

CONDA_PKGS_DIRS = None
MAMBA_ROOT_PREFIX = None


def _ensureconda(
    mamba: bool = False,
    micromamba: bool = False,
    conda: bool = False,
    conda_exe: bool = False,
):
    _conda_exe = ensureconda.ensureconda(
        mamba=mamba,
        micromamba=micromamba,
        conda=conda,
        conda_exe=conda_exe,
    )

    return _conda_exe


def _determine_conda_executable(
    conda_executable: Optional[str], mamba: bool, micromamba: bool
):
    if conda_executable:
        if pathlib.Path(conda_executable).exists():
            yield conda_executable
        yield shutil.which(conda_executable)

    yield _ensureconda(mamba=mamba, micromamba=micromamba, conda=True, conda_exe=True)


def determine_conda_executable(
    conda_executable: Optional[str], mamba: bool, micromamba: bool
):
    for candidate in _determine_conda_executable(conda_executable, mamba, micromamba):
        if candidate is not None:
            if is_micromamba(candidate):
                if ensureconda.api.determine_micromamba_version(
                    candidate
                ) < LooseVersion("0.17"):
                    mamba_root_prefix()
            return candidate
    raise RuntimeError("Could not find conda (or compatible) executable")


def _invoke_conda(
    conda: PathLike,
    prefix: str,
    name: str,
    command_args: Sequence[PathLike],
    post_args: Sequence[PathLike] = [],
    check_call: bool = False,
) -> subprocess.Popen:
    """
    Invoke external conda executable

    Parameters
    ----------
    conda :
        Path to conda, mamba, or micromamba
    prefix :
        Prefix of target env
    name :
        Name of target env
    command_args :
        Arguments to conda executable
    post_args :
        Optional arguments to append to command_args
    check_call :
        If True, raise CalledProcessError if conda returns != 0

    """
    if prefix and name:
        raise ValueError("Provide either prefix, or name, but not both.")
    common_args = []
    if prefix:
        common_args.append("--prefix")
        common_args.append(prefix)
    if name:
        common_args.append("--name")
        common_args.append(name)
    conda_flags = os.environ.get("CONDA_FLAGS")
    if conda_flags:
        common_args.extend(shlex.split(conda_flags))

    cmd = [str(conda), *command_args, *common_args, *post_args]

    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        universal_newlines=True,
    ) as p:
        if p.stdout:
            for line in _process_stdout(p.stdout):
                logging.info(line)

        if p.stderr:
            for line in p.stderr:
                logging.error(line.rstrip())

    if check_call and p.returncode != 0:
        raise subprocess.CalledProcessError(
            p.returncode, [str(conda), *command_args, *common_args, *post_args]
        )

    return p


def _process_stdout(stdout):
    cache = set()
    extracting_packages = False
    leading_empty = True
    for logline in stdout:
        logline = logline.rstrip()
        if logline:
            leading_empty = False
        if logline == "Downloading and Extracting Packages":
            extracting_packages = True
        if not logline and (extracting_packages or leading_empty):
            continue
        if "%" in logline:
            logline = logline.split()[0]
            if logline not in cache:
                yield logline
                cache.add(logline)
        else:
            yield logline


def conda_env_override(platform) -> Dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "CONDA_SUBDIR": platform,
            "CONDA_PKGS_DIRS": conda_pkgs_dir(),
            "CONDA_UNSATISFIABLE_HINTS_CHECK_DEPTH": "0",
            "CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY": "False",
        }
    )
    return env


def _get_conda_flags(channels: Sequence[str], platform) -> List[str]:
    args = []
    conda_flags = os.environ.get("CONDA_FLAGS")
    if conda_flags:
        args.extend(shlex.split(conda_flags))
    if channels:
        args.append("--override-channels")

    for channel in channels:
        args.extend(["--channel", channel])
        if channel == "defaults" and platform in {"win-64", "win-32"}:
            # msys2 is a windows-only channel that conda automatically
            # injects if the host platform is Windows. If our host
            # platform is not Windows, we need to add it manually
            args.extend(["--channel", "msys2"])
    return args


def conda_pkgs_dir():
    global CONDA_PKGS_DIRS
    if CONDA_PKGS_DIRS is None:
        temp_dir = tempfile.TemporaryDirectory()
        CONDA_PKGS_DIRS = temp_dir.name
        atexit.register(temp_dir.cleanup)
        return CONDA_PKGS_DIRS
    else:
        return CONDA_PKGS_DIRS


def mamba_root_prefix():
    """Legacy root prefix used by micromamba"""
    global MAMBA_ROOT_PREFIX
    if MAMBA_ROOT_PREFIX is None:
        temp_dir = tempfile.TemporaryDirectory()
        MAMBA_ROOT_PREFIX = temp_dir.name
        atexit.register(temp_dir.cleanup)
        os.environ["MAMBA_ROOT_PREFIX"] = MAMBA_ROOT_PREFIX
        return MAMBA_ROOT_PREFIX
    else:
        return MAMBA_ROOT_PREFIX


def reset_conda_pkgs_dir():
    """Clear the fake conda packages directory.  This is used only by testing"""
    global CONDA_PKGS_DIRS
    global MAMBA_ROOT_PREFIX
    CONDA_PKGS_DIRS = None
    MAMBA_ROOT_PREFIX = None
    if "CONDA_PKGS_DIRS" in os.environ:
        del os.environ["CONDA_PKGS_DIRS"]
    if "MAMBA_ROOT_PREFIX" in os.environ:
        del os.environ["MAMBA_ROOT_PREFIX"]


def is_micromamba(conda: PathLike) -> bool:
    return str(conda).endswith("micromamba") or str(conda).endswith("micromamba.exe")
