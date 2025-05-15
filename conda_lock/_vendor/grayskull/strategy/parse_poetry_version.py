from __future__ import annotations

import re

from packaging.version import Version

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


def parse_version(version: str) -> dict[str, int | None]:
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


def get_padded_base_version(version: str | Version) -> str:
    """
    Returns the same PEP440 version padded with zeroes if
    minor or micro are not specified.

    >>> get_padded_base_version("0.2.3")
    '0.2.3'
    >>> get_padded_base_version("1")
    '1.0.0'
    >>> get_padded_base_version("1.2")
    '1.2.0'
    >>> get_padded_base_version("1.2.3")
    '1.2.3'
    >>> get_padded_base_version("1.2.3.post1")
    '1.2.3.post1'
    >>> get_padded_base_version("2!1.2.post1")
    '2!1.2.0.post1'
    """
    if not isinstance(version, Version):
        version = Version(version)

    # Start with the normalized release
    floor = f"{version.major}.{version.minor}.{version.micro}"

    # Add other components as they appear
    if version.epoch is not None and version.epoch > 0:
        floor = f"{version.epoch}!{floor}"  # Add epoch if present
    if version.pre is not None:
        floor += (
            f"{version.pre[0]}{version.pre[1]}"  # Add pre-release (e.g., a1, b1, rc1)
        )
    if version.post is not None:
        floor += f".post{version.post}"  # Add post-release (e.g., .post1)
    if version.dev is not None:
        floor += f".dev{version.dev}"  # Add development release (e.g., .dev1)
    if version.local is not None:
        floor += f"+{version.local}"  # Add local metadata (e.g., +local)
    return floor


def get_caret_ceiling(version: str | Version) -> str:
    """
    Accepts a Poetry caret version and returns the exclusive version ceiling.

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
    >>> get_caret_ceiling("1.2.3.post1")
    '2.0.0'
    >>> get_caret_ceiling("2!1.2.3.post1")
    '2.0.0'
    """
    if not isinstance(version, Version):
        version = Version(version)
    # Determine the upper bound
    if version.major > 0 or len(version.release) == 1:
        ceiling = f"{version.major + 1}.0.0"
    elif version.minor > 0 or len(version.release) == 2:
        ceiling = f"0.{version.minor + 1}.0"
    else:
        ceiling = f"0.0.{version.micro + 1}"
    return ceiling


def get_tilde_ceiling(version: str | Version) -> str:
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
    if not isinstance(version, Version):
        version = Version(version)
    # Determine the upper bound based on the specified components
    if len(version.release) in [2, 3]:  # Major, Minor, Micro, or Major, Minor
        tilde_ceiling = f"{version.major}.{version.minor + 1}.0"
    else:  # Major, Minor or Only Major
        tilde_ceiling = f"{version.major + 1}.0.0"
    return tilde_ceiling


def encode_poetry_version(poetry_specifier: str) -> str:
    """
    Encodes Poetry version specifier as a Conda version specifier.

    Example: ^1 => >=1.0.0,<2.0.0

    # should be unchanged
    >>> encode_poetry_version("~=1.1")
    '~=1.1'
    >>> encode_poetry_version("1.*")
    '1.*'
    >>> encode_poetry_version(">=1,<2")
    '>=1,<2'
    >>> encode_poetry_version("==1.2.3")
    '==1.2.3'
    >>> encode_poetry_version("!=1.2.3")
    '!=1.2.3'
    >>> encode_poetry_version("===1.2.3")
    '===1.2.3'

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

    # handle caret operator with a PEP440 version
    # correctly
    >>> encode_poetry_version("^0.8.post1")
    '>=0.8.0.post1,<0.9.0'

    # handle tilde operator correctly
    # examples from Poetry docs
    >>> encode_poetry_version("~1")
    '>=1.0.0,<2.0.0'
    >>> encode_poetry_version("~1.2")
    '>=1.2.0,<1.3.0'
    >>> encode_poetry_version("~1.2.3")
    '>=1.2.3,<1.3.0'

    # # handle tilde operator with a PEP440 version
    # # correctly
    # >>> encode_poetry_version("~0.8.post1")
    # '>=0.8.0.post1,<0.9.0.a0'

    # handle or operator correctly
    >>> encode_poetry_version("1.2.3|1.2.4")
    '1.2.3|1.2.4'
    >>> encode_poetry_version("^5|| ^6 | ^7")
    '>=5.0.0,<6.0.0|>=6.0.0,<7.0.0|>=7.0.0,<8.0.0'
    """
    if "|" in poetry_specifier:
        poetry_or_clauses = [clause.strip() for clause in poetry_specifier.split("|")]
        conda_or_clauses = [
            encode_poetry_version(clause)
            for clause in poetry_or_clauses
            if clause != ""
        ]
        return "|".join(conda_or_clauses)

    poetry_clauses = poetry_specifier.split(",")

    conda_clauses = []
    for poetry_clause in poetry_clauses:
        poetry_clause = poetry_clause.replace(" ", "")
        if poetry_clause.startswith("^"):
            # handle ^ operator
            caret_version = Version(poetry_clause[1:])
            floor = get_padded_base_version(caret_version)
            ceiling = get_caret_ceiling(caret_version)
            conda_clauses.append(">=" + floor)
            conda_clauses.append("<" + ceiling)
            continue

        if poetry_clause.startswith("~="):
            # handle the compatible release operator ~=
            # before the tilde ~ operator
            conda_clauses.append(poetry_clause)
            continue

        if poetry_clause.startswith("~"):
            # handle ~ operator
            tilde_version = poetry_clause[1:]
            floor = get_padded_base_version(tilde_version)
            ceiling = get_tilde_ceiling(tilde_version)
            conda_clauses.append(">=" + floor)
            conda_clauses.append("<" + ceiling)
            continue

        # other poetry clauses should be conda-compatible
        conda_clauses.append(poetry_clause)

    return ",".join(conda_clauses)


def encode_poetry_platform_to_selector_item(poetry_platform: str) -> str:
    """
    Encodes Poetry Platform specifier as a Conda selector.

    Example: "darwin" => "osx"
    """

    platform_selectors = {"windows": "win", "linux": "linux", "darwin": "osx"}
    poetry_platform = poetry_platform.lower().strip()
    if poetry_platform in platform_selectors:
        return platform_selectors[poetry_platform]
    else:  # unknown
        return ""


def encode_poetry_python_version_to_selector_item(poetry_specifier: str) -> str:
    """
    Encodes Poetry Python version specifier set as a Conda selector.

    Example:
        ">=3.8,<3.12" => "py>=38 and py<312"
        ">=3.8,<3.12,!=3.11" => "py>=38 and py<312 and py!=311"
        "<3.8|>=3.10" => "py<38 or py>=310"
        "<3.8|>=3.10,!=3.11" => "py<38 or py>=310 and py!=311"

    # handle exact version specifiers correctly
    >>> encode_poetry_python_version_to_selector_item("3")
    'py==3'
    >>> encode_poetry_python_version_to_selector_item("3.8")
    'py==38'
    >>> encode_poetry_python_version_to_selector_item("==3.8")
    'py==38'
    >>> encode_poetry_python_version_to_selector_item("!=3.8")
    'py!=38'
    >>> encode_poetry_python_version_to_selector_item("!=3.8.1")
    'py!=38'

    # handle caret operator correctly
    >>> encode_poetry_python_version_to_selector_item("^3.10") # '>=3.10.0,<4.0.0'
    'py>=310 and py<4'

    # handle tilde operator correctly
    >>> encode_poetry_python_version_to_selector_item("~3.10") # '>=3.10.0,<3.11.0'
    'py>=310 and py<311'

    # handle multiple requirements correctly (in "and")
    >>> encode_poetry_python_version_to_selector_item(">=3.8,<3.12,!=3.11")
    'py>=38 and py<312 and py!=311'

    # handle multiple requirements in "or" correctly ("and" takes precendence)
    >>> encode_poetry_python_version_to_selector_item("<3.8|>=3.10,!=3.11")
    'py<38 or py>=310 and py!=311'

    # handle compatible release operator correctly
    >>> encode_poetry_python_version_to_selector_item("~=3")
    'py>=3'
    >>> encode_poetry_python_version_to_selector_item("~=3.8")
    'py>=38 and py<4'
    >>> encode_poetry_python_version_to_selector_item("~=3.8.1")
    'py==38'
    >>> encode_poetry_python_version_to_selector_item("~=3.8.0.1")
    'py==38'
    >>> encode_poetry_python_version_to_selector_item("~=3.8,!=3.11")
    'py>=38 and py<4 and py!=311'

    # handle wildcard versions correctly
    >>> encode_poetry_python_version_to_selector_item("*")
    ''
    >>> encode_poetry_python_version_to_selector_item("3.*,!=3.11")
    'py>=3 and py<4 and py!=311'
    >>> encode_poetry_python_version_to_selector_item("!=3.*|3.11")
    'py<3 or py>=4 or py==311'
    >>> encode_poetry_python_version_to_selector_item("!=3.*,!=4.1")
    '(py<3 or py>=4) and py!=41'

    """

    if not poetry_specifier:
        return ""

    version_specifier = encode_poetry_version(poetry_specifier)

    if "|" in version_specifier:
        poetry_or_clauses = [clause.strip() for clause in version_specifier.split("|")]
        conda_or_clauses = [
            encode_poetry_python_version_to_selector_item(clause)
            for clause in poetry_or_clauses
            if clause != ""
        ]
        conda_or_clauses = " or ".join(conda_or_clauses)
        return conda_or_clauses

    conda_clauses = version_specifier.split(",")

    conda_selectors = []
    for conda_clause in conda_clauses:
        conda_selector = parse_python_version_specifier_to_selector(conda_clause)
        if conda_selector != "":
            conda_selectors.append(conda_selector)
    if len(conda_selectors) > 1:
        conda_selectors = [
            conda_selector if " or " not in conda_selector else f"({conda_selector})"
            for conda_selector in conda_selectors
        ]
    selectors = " and ".join(conda_selectors)
    return selectors


def parse_python_version_specifier_to_selector(version_specifier: str):
    """
    Take a Python version specifier, PEP 440 compliant.

    Return Python version conda selector.

    If the version_specifier has no operator, the equal operator ==
    is assumed.

    The version is normalized to "major.minor" (drop patch if present)
    or only "major" if minor is 0 (e.g. "3.8" -> "38", "3.8.1" -> "38",
    "3.0" -> "3", "3.0.1" -> "3").

    The compatible release operator ~= is expanded eventually into two selector
    items if the version has major and minor (e.g. "~=3.8" -> "py>=38 and py<4",
    but "~=3.8.1" -> "py==38").

    The exact equality operators == and != support the wildcard *
    in the version (e.g. "*" -> "==*" -> "").

    Examples:
        ">=3.8"   ->   "py>=38"
        "3.12"    ->   "py==312"
        "~=3.8"   ->   "py>=38 and py<4"
        "~=3.8.1" ->   "py==38"
        "3.*"     ->   "py>=3 and py<4"
        "!=3.*"   ->   "py<3 or py>=4"

    >>> parse_python_version_specifier_to_selector(">=3.8")
    'py>=38'
    >>> parse_python_version_specifier_to_selector("3.12")
    'py==312'
    >>> parse_python_version_specifier_to_selector("<4.0.0")
    'py<4'
    >>> parse_python_version_specifier_to_selector("<4.0.0.1")
    'py<4'
    >>> parse_python_version_specifier_to_selector(">=3")
    'py>=3'
    >>> parse_python_version_specifier_to_selector(">=3.8.0")
    'py>=38'
    >>> parse_python_version_specifier_to_selector(">=3.8.0.1")
    'py>=38'
    >>> parse_python_version_specifier_to_selector("~=3.8")
    'py>=38 and py<4'
    >>> parse_python_version_specifier_to_selector("3.*")
    'py>=3 and py<4'
    >>> parse_python_version_specifier_to_selector("!=3.*")
    'py<3 or py>=4'

    """
    # Regex to split an optional operator and a whatever version
    pattern = r"^(?P<operator>\^|~=|~|>=|<=|>|<|!=|===|==|=)?(?P<version>.+)$"

    # Here Specifier or Version are not useful because
    # Specifier requires an operator, and Version cannot
    # accept an operator. Doomed to match twice.

    match = re.match(pattern, version_specifier)
    if not match:
        raise ValueError(f"Invalid version selector: {version_specifier}")

    # Extract operator and version
    operator = match.group("operator")
    version = match.group("version")

    if operator in [None, "=", "==", "==="]:
        # Default to "==" if no operator is provided or "=", "==="
        operator = "=="
        # Check also if there is a wildcard operator "*" (may result in two operators)
        return expand_operator_wildcard_version_to_selector(operator, version)
    elif operator == "~=":
        # Compatible release operator "~=" (may result in two operators)
        return expand_compatible_release_operator_version_to_selector(version)
    elif operator == "!=":
        # Check also if there is a wildcard operator "*" (may result in two operators)
        return expand_operator_wildcard_version_to_selector(operator, version)
    return operator_version_to_selector(operator, Version(version))


def expand_compatible_release_operator_version_to_selector(
    version: str | Version,
) -> str:
    """
    Take a Python version, PEP440 compliant.

    The compatible release operator "~=" is implicit.

    The python version should be reasonable and realistic (e.g. "3.11"), but
    it is true that an esoteric still PEP440 valid version would make grayskull
    crash, therefore here we parse the Python version as a PEP440 compliant
    version (e.g. "3.11.3.dev0").

    The compatible release operator ~= is expanded eventually into two selector
    items if the version has major and minor (e.g. "~=3.8" -> "py>=38 and py<4",
    but "~=3.8.1" -> "py==38", and "~=3" -> "py>=38").

    The reason why this operator is expanded here and not in
    encode_poetry_python_version_to_selector_item just after
    encode_poetry_version is because the selectors for the python
    version use only major and minor, and therefore the compatible
    release operator ~= makes sense only in the case of major and
    minor specified (e.g. "~=3.8" -> "py>=38 and py<4") and just by
    knowing that we can avoid having to detect cases like:
        "~=3.8.0.1" -> ">=3.8.0.1, ==3.8.0.*" -> ">=3.8.0.1, <3.8.1.0a" -> "py==38"
    and expand only for:
        "~=3.8" -> ">=3.8, ==3.*" -> ">=3.8, <4.0a" -> "py>=38 and py<4"
        "~=3.0" -> ">=3.0, ==3.*" -> ">=3.0, <4.0a" -> "py>=3 and py<4"
    in the rest of the cases it's a simple conversion to ">=" operator.

    If we would expand it before, we would receive specifier sets like
    ">=3.8.0.1, <3.8.1.0a" (among other specifiers) and we would need to
    detect those cases to avoid rendering to a naive "py>=38 and py<38"
    which would be an invalid statement.

    Rationale:
        - generally it would work in this way:
            ~=2     -> illegal for PEP440
            ~=2.2   -> ">=2.2, ==2.*" -> ">=2.2, <3.0a"
            ~=1.4.5 -> ">=1.4.5, ==1.4.*" -> ">=1.4.5, <1.5.0a"
            ~=0.5.3 -> ">=0.5.3, ==0.5.*" -> ">=0.5.3, <0.6.0a"
        - considering only python versions and their selectors:
            ~=3       -> illegal for PEP440     -> ">=3, ==*" -> ">=3" -> "py>=3"
            ~=3.8     -> ">=3.8, ==3.*"         -> ">=3.8, <4.0a" -> "py>=38 and py<4"
            ~=3.8.1   -> ">=3.8.1, ==3.8.*"     -> ">=3.8.1, <3.9.0a" -> "py==38"
            ~=3.8.0.1 -> ">=3.8.0.1, ==3.8.0.*" -> ">=3.8.0.1, <3.8.1.0a" -> "py==38"

    Examples:
        "3"     ->   "py>=3"
        "3.8"   ->   "py>=38 and py<4"
        "3.8.1" ->   "py==38"

    >>> expand_compatible_release_operator_version_to_selector("3")
    'py>=3'
    >>> expand_compatible_release_operator_version_to_selector("3.8")
    'py>=38 and py<4'
    >>> expand_compatible_release_operator_version_to_selector("3.0")
    'py>=3 and py<4'
    >>> expand_compatible_release_operator_version_to_selector("3.8.1")
    'py==38'
    >>> expand_compatible_release_operator_version_to_selector("3.8.1.1")
    'py==38'
    >>> expand_compatible_release_operator_version_to_selector("3.8a0")
    'py>=38 and py<4'
    >>> expand_compatible_release_operator_version_to_selector("3.8.1.post1")
    'py==38'
    """
    if not isinstance(version, Version):
        version = Version(version)

    # The compatible release operator ~= is expanded eventually
    # into two selector items if the version has major and minor
    # even if the minor is 0, because it would be used as a padding
    # placeholder.
    # See:
    # https://packaging.python.org/en/latest/specifications/version-specifiers/#compatible-release
    if len(version.release) < 3:
        # "3.8" -> "py>=38 and py<4", and "3" -> "py>=3"
        lower_bound_operator = ">="
    else:
        # "3.8.1" -> "py==38"
        lower_bound_operator = "=="
    lower_bound_selector = operator_version_to_selector(lower_bound_operator, version)
    if len(version.release) == 2:
        # get version selector, with ">=" operator as lower bound
        # get the ceiling of the version (major bumped by 1)
        ceiling_version = version.major + 1
        # get ceiling version selector, with "<" operator as upper bound
        upper_bound = operator_version_to_selector("<", Version(f"{ceiling_version}"))
        return f"{lower_bound_selector} and {upper_bound}"
    return lower_bound_selector


def expand_operator_wildcard_version_to_selector(
    operator: str | None, version: str | Version
) -> str:
    """
    Take the strict equality operators "==" or "!=" and
    a Python version ending with ".*", PEP440 compliant.

    "*" is accepted, but "1*" or "1.1*" are not accepted
    because PEP 440 requires the "*" wildcard to follow a "."
    because it is meant to represent a "range of versions
    with common prefix components."

    Wildcards can be expressed as ranges (">=" and "<") using
    the next significant component.

    Examples:
         "*" -> "==*" -> ""
         "1.*" -> ">=1.0.0.a0,<2.0.0"
         "1.1.*" -> ">=1.1.0.a0,<1.2.0"
         "1.1.1.*" -> ">=1.1.1.a0,<1.1.2"

    inclusion: 1.1
            == 1.1        # Equal, so 1.1 matches clause
            == 1.1.0      # Zero padding expands 1.1 to 1.1.0, so it matches clause
            == 1.1.dev1   # Not equal (dev-release), so 1.1 does not match clause
            == 1.1a1      # Not equal (pre-release), so 1.1 does not match clause
            == 1.1.post1  # Not equal (post-release), so 1.1 does not match clause
            == 1.1.*      # Same prefix, so 1.1 matches clause

    exclusion: 1.1.post1
            != 1.1        # Not equal, so 1.1.post1 matches clause
            != 1.1.post1  # Equal, so 1.1.post1 does not match clause
            != 1.1.*      # Same prefix, so 1.1.post1 does not match clause

    # In practice, if the star suffix ".*" is used on Python version specifiers
    # to be rendered as conda selector, we can simplify the calculation according
    # to which operator is used:
    #
    # - equality: it makes sense to expand it only if the version is "{major}.*"
    #             (e.g. "3.*" -> ">=3.0a0,<4" -> "py>=3 and py<4"). In all the
    #             other cases it is enough to remove the ".*" and consider as
    #             usual the "{operator}{major}{minor}" if "minor" is more than
    #             "0", otherwise "{operator}{major}".

    # - inequality: it makes sense to expand it only if the version is "{major}.*"
    #             (e.g. "3.*" -> ">=3.0a0,<4" -> "py>=3 and py<4"). In all the
    #             other cases it is enough to remove the ".*" and consider as
    #             usual the "{operator}{major}{minor}" if "minor" is more than
    #             "0", otherwise "{operator}{major}".

    # Equality examples

    >>> expand_operator_wildcard_version_to_selector("==","*") # any
    ''

    >>> expand_operator_wildcard_version_to_selector("==", "3.*") # >=3.0a0,<4
    'py>=3 and py<4'

    # >=3.12.0a0,<3.13
    >>> expand_operator_wildcard_version_to_selector("==", "3.12.*")
    'py==312'

    # >=3.9.1.0a0,<3.9.2
    >>> expand_operator_wildcard_version_to_selector("==", "3.9.1.*")
    'py==39'

    # >=3.9.1.0a0,<3.9.1.2
    >>> expand_operator_wildcard_version_to_selector("==", "3.9.1.1.*")
    'py==39'

    # Inequality examples

    >>> expand_operator_wildcard_version_to_selector("!=","*") # none
    'py<0'

    # <3.0a0|>=4
    >>> expand_operator_wildcard_version_to_selector("!=", "3.*")
    'py<3 or py>=4'

    # <3.12.0a0|>=3.13
    >>> expand_operator_wildcard_version_to_selector("!=", "3.12.*")
    'py<312 or py>=313'

    # <3.9.1.0a0|>=3.9.2
    >>> expand_operator_wildcard_version_to_selector("!=", "3.9.1.*")
    'py<39 or py>=39'

    # <3.9.1.1.0a0|>=3.9.1.2
    >>> expand_operator_wildcard_version_to_selector("!=", "3.9.1.1.*")
    'py<39 or py>=39'

    """
    if version == "*":
        # This should not happen, as the "*" is stripped away before
        # to avoid having trivial selectors, but consider it anyway
        # for general usage.
        if operator in [None, "", "=", "==", "==="]:
            return ""
        else:
            return "py<0"
    base_version = version.rstrip(".*")
    expand_operator_wildcard_version = len(base_version) != len(version)
    version = Version(base_version)
    if operator in [None, "", "=", "==", "==="]:
        # Default to "==" if no operator is provided or "=", "==="
        operator = "=="
        # it makes sense to expand it only if the version is "{major}.*"
        if expand_operator_wildcard_version and len(version.release) == 1:
            # "3.*" -> ">=3.0a0,<4" -> "py>=3 and py<4"
            left_bound_selector = operator_version_to_selector(">=", version)
            # get the ceiling of the version (major bumped by 1)
            right_version = version.major + 1
            # get ceiling version selector, with "<" operator as upper bound
            right_bound_selector = operator_version_to_selector(
                "<", Version(f"{right_version}")
            )
            return f"{left_bound_selector} and {right_bound_selector}"
    elif operator == "!=":
        if expand_operator_wildcard_version:
            left_bound_selector = operator_version_to_selector("<", version)
            if len(version.release) == 1:
                # "3.*" -> "<3.0a0|>=4" -> "py<3 or py>=4"
                # major bumped by 1
                right_version = version.major + 1
            elif len(version.release) == 2:
                # "3.12.*" -> "<3.12.0a0|>=3.13" -> "py<312 or py>=313"
                # minor bumped by 1
                right_version = f"{version.major}." + str(version.minor + 1)
            else:
                # "3.9.1.*" -> <3.9.1.0a0|>=3.9.2"" -> "py<39 or py>=39"
                # "3.9.1.1.*" -> <3.9.1.1.0a0|>=3.9.1.2"" -> "py<39 or py>=39"
                # use the same version in the right bound
                right_version = f"{version.major}.{version.minor}"
            # get ceiling version selector, with ">=" operator as upper bound
            right_bound_selector = operator_version_to_selector(
                ">=", Version(f"{right_version}")
            )
            return f"{left_bound_selector} or {right_bound_selector}"
    return operator_version_to_selector(operator, version)


def operator_version_to_selector(operator: str | None, version: str | Version) -> str:
    """
    Consider major, minor, and discard the rest (patch or additional parts)
    Return only major if minor is "0", otherwise return major.minor

    >>> operator_version_to_selector(">=", "3.8")
    'py>=38'
    >>> operator_version_to_selector("==", "3.12")
    'py==312'
    >>> operator_version_to_selector("", "3.12")
    'py==312'
    >>> operator_version_to_selector("<", "4.0.0")
    'py<4'
    >>> operator_version_to_selector("<", "4.0.0.1")
    'py<4'
    >>> operator_version_to_selector(">=", "3")
    'py>=3'
    >>> operator_version_to_selector(">=", "3.8.0")
    'py>=38'
    >>> operator_version_to_selector(">=", "3.8.0.1")
    'py>=38'
    >>> operator_version_to_selector(">=", "3.8.0.1post1")
    'py>=38'
    >>> operator_version_to_selector(">=", "3.8.0.1a0")
    'py>=38'
    >>> operator_version_to_selector("<", "2!4.0.0.1.post1")
    'py<4'
    """
    if operator in [None, "", "=", "==="]:
        # Default to "==" if no operator is provided or "=", "==="
        operator = "=="
    if not isinstance(version, Version):
        version = Version(version)
    version_selector = (
        version.major if version.minor == 0 else f"{version.major}{version.minor}"
    )
    return f"py{operator}{version_selector}"


def combine_conda_selectors(python_selector: str, platform_selector: str):
    """
    Combine selectors based on presence
    """
    if python_selector and platform_selector:
        if " or " in python_selector:
            python_selector = f"({python_selector})"
        selector = f"{python_selector} and {platform_selector}"
    elif python_selector:
        selector = f"{python_selector}"
    elif platform_selector:
        selector = f"{platform_selector}"
    else:
        selector = ""
    return f"  # [{selector}]" if selector else ""
