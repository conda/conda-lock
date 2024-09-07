import re
from typing import Dict, Optional

import semver

VERSION_REGEX = re.compile(
    r"""^[vV]?
        (?P<major>0|[1-9]\d*)
        (\.
        (?P<minor>0|[1-9]\d*)
        (\.
            (?P<patch>0|[1-9]\d*)
        )?
        )?$
    """,
    re.VERBOSE,
)


class InvalidVersion(BaseException):
    pass


def parse_version(version: str) -> Dict[str, Optional[int]]:
    """
    Parses a version string (not necessarily semver) to a dictionary with keys
    "major", "minor", and "patch". "minor" and "patch" are possibly None.

    >>> parse_version("0")
    {'major': 0, 'minor': None, 'patch': None}
    >>> parse_version("1")
    {'major': 1, 'minor': None, 'patch': None}
    >>> parse_version("1.2")
    {'major': 1, 'minor': 2, 'patch': None}
    >>> parse_version("1.2.3")
    {'major': 1, 'minor': 2, 'patch': 3}
    """
    match = VERSION_REGEX.search(version)
    if not match:
        raise InvalidVersion(f"Could not parse version {version}.")

    return {
        key: None if value is None else int(value)
        for key, value in match.groupdict().items()
    }


def vdict_to_vinfo(version_dict: Dict[str, Optional[int]]) -> semver.VersionInfo:
    """
    Coerces version dictionary to a semver.VersionInfo object. If minor or patch
    numbers are missing, 0 is substituted in their place.
    """
    ver = {key: 0 if value is None else value for key, value in version_dict.items()}
    return semver.VersionInfo(**ver)


def coerce_to_semver(version: str) -> str:
    """
    Coerces a version string to a semantic version.
    """
    if semver.VersionInfo.is_valid(version):
        return version

    parsed_version = parse_version(version)
    vinfo = vdict_to_vinfo(parsed_version)
    return str(vinfo)


def get_caret_ceiling(target: str) -> str:
    """
    Accepts a Poetry caret target and returns the exclusive version ceiling.

    Targets that are invalid semver strings (e.g. "1.2", "0") are handled
    according to the Poetry caret requirements specification, which is based on
    whether the major version is 0:

    - If the major version is 0, the ceiling is determined by bumping the
    rightmost specified digit and then coercing it to semver.
    Example: 0 => 1.0.0, 0.1 => 0.2.0, 0.1.2 => 0.1.3

    - If the major version is not 0, the ceiling is determined by
    coercing it to semver and then bumping the major version.
    Example: 1 => 2.0.0, 1.2 => 2.0.0, 1.2.3 => 2.0.0

    # Examples from Poetry docs
    >>> get_caret_ceiling("0")
    '1.0.0'
    >>> get_caret_ceiling("0.0")
    '0.1.0'
    >>> get_caret_ceiling("0.0.3")
    '0.0.4'
    >>> get_caret_ceiling("0.2.3")
    '0.3.0'
    >>> get_caret_ceiling("1")
    '2.0.0'
    >>> get_caret_ceiling("1.2")
    '2.0.0'
    >>> get_caret_ceiling("1.2.3")
    '2.0.0'
    """
    if not semver.VersionInfo.is_valid(target):
        target_dict = parse_version(target)

        if target_dict["major"] == 0:
            if target_dict["minor"] is None:
                target_dict["major"] += 1
            elif target_dict["patch"] is None:
                target_dict["minor"] += 1
            else:
                target_dict["patch"] += 1
            return str(vdict_to_vinfo(target_dict))

        vdict_to_vinfo(target_dict)
        return str(vdict_to_vinfo(target_dict).bump_major())

    target_vinfo = semver.VersionInfo.parse(target)

    if target_vinfo.major == 0:
        if target_vinfo.minor == 0:
            return str(target_vinfo.bump_patch())
        else:
            return str(target_vinfo.bump_minor())
    else:
        return str(target_vinfo.bump_major())


def get_tilde_ceiling(target: str) -> str:
    """
    Accepts a Poetry tilde target and returns the exclusive version ceiling.

    # Examples from Poetry docs
    >>> get_tilde_ceiling("1")
    '2.0.0'
    >>> get_tilde_ceiling("1.2")
    '1.3.0'
    >>> get_tilde_ceiling("1.2.3")
    '1.3.0'
    """
    target_dict = parse_version(target)
    if target_dict["minor"]:
        return str(vdict_to_vinfo(target_dict).bump_minor())

    return str(vdict_to_vinfo(target_dict).bump_major())


def encode_poetry_version(poetry_specifier: str) -> str:
    """
    Encodes Poetry version specifier as a Conda version specifier.

    Example: ^1 => >=1.0.0,<2.0.0

    # should be unchanged
    >>> encode_poetry_version("1.*")
    '1.*'
    >>> encode_poetry_version(">=1,<2")
    '>=1,<2'
    >>> encode_poetry_version("==1.2.3")
    '==1.2.3'
    >>> encode_poetry_version("!=1.2.3")
    '!=1.2.3'

    # strip spaces
    >>> encode_poetry_version(">= 1, < 2")
    '>=1,<2'

    # handle exact version specifiers correctly
    >>> encode_poetry_version("1.2.3")
    '1.2.3'
    >>> encode_poetry_version("==1.2.3")
    '==1.2.3'

    # handle caret operator correctly
    # examples from Poetry docs
    >>> encode_poetry_version("^0")
    '>=0.0.0,<1.0.0'
    >>> encode_poetry_version("^0.0")
    '>=0.0.0,<0.1.0'
    >>> encode_poetry_version("^0.0.3")
    '>=0.0.3,<0.0.4'
    >>> encode_poetry_version("^0.2.3")
    '>=0.2.3,<0.3.0'
    >>> encode_poetry_version("^1")
    '>=1.0.0,<2.0.0'
    >>> encode_poetry_version("^1.2")
    '>=1.2.0,<2.0.0'
    >>> encode_poetry_version("^1.2.3")
    '>=1.2.3,<2.0.0'

    # handle tilde operator correctly
    # examples from Poetry docs
    >>> encode_poetry_version("~1")
    '>=1.0.0,<2.0.0'
    >>> encode_poetry_version("~1.2")
    '>=1.2.0,<1.3.0'
    >>> encode_poetry_version("~1.2.3")
    '>=1.2.3,<1.3.0'
    """
    poetry_clauses = poetry_specifier.split(",")

    conda_clauses = []
    for poetry_clause in poetry_clauses:
        poetry_clause = poetry_clause.replace(" ", "")
        if poetry_clause.startswith("^"):
            # handle ^ operator
            target = poetry_clause[1:]
            floor = coerce_to_semver(target)
            ceiling = get_caret_ceiling(target)
            conda_clauses.append(">=" + floor)
            conda_clauses.append("<" + ceiling)
            continue

        if poetry_clause.startswith("~"):
            # handle ~ operator
            target = poetry_clause[1:]
            floor = coerce_to_semver(target)
            ceiling = get_tilde_ceiling(target)
            conda_clauses.append(">=" + floor)
            conda_clauses.append("<" + ceiling)
            continue

        # other poetry clauses should be conda-compatible
        conda_clauses.append(poetry_clause)

    return ",".join(conda_clauses)
