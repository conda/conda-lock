import re

from pathlib import Path

import tomlkit


def test_poetry_vendored_version_rule_matches_vendored_version():
    """Check consistency of the vendoring substitution rule with the vendored version.

    The version from the vendoring substitution rule is defined in pyproject.toml
    under the list `tool.vendoring.transformations.substitute`. The relevant line
    looks like:

    ```toml
    { match = '__version__ = metadata.version("poetry")', replace = '__version__ = "2.0.1"' },
    ```

    This should match with the vendored version specified in
    `conda_lock/_vendor/vendor.txt` on the line that looks like:

    ```text
    poetry==2.0.1
    ```
    """

    # Get the project root directory
    repo_root = Path(__file__).parent.parent

    # Read the pyproject.toml file
    pyproject_path = repo_root / "pyproject.toml"
    pyproject_content = tomlkit.parse(pyproject_path.read_text())

    # Extract the version from the substitution rule
    transform_section = (
        pyproject_content.get("tool", {})
        .get("vendoring", {})
        .get("transformations", {})
    )
    substitution_rules = transform_section.get("substitute", [])
    version_substitution = None

    for rule in substitution_rules:
        match_value = rule.get("match", "")
        if r'__version__ = metadata\.version\("poetry"\)' in match_value:
            version_substitution = rule.get("replace", "")
            break

    assert version_substitution is not None, (
        "Could not find version substitution rule in pyproject.toml"
    )

    # Extract the version number from the substitution string using regex
    pyproject_version_match = re.search(
        r'"([0-9]+\.[0-9]+\.[0-9]+)"', version_substitution
    )
    assert pyproject_version_match, (
        f"Could not extract version from substitution: {version_substitution}"
    )
    pyproject_version = pyproject_version_match.group(1)

    # Read the vendor.txt file
    vendor_path = repo_root / "conda_lock" / "_vendor" / "vendor.txt"
    vendor_content = vendor_path.read_text()

    # Find the poetry version in vendor.txt
    poetry_line_match = re.search(r"poetry==([0-9]+\.[0-9]+\.[0-9]+)", vendor_content)
    assert poetry_line_match, "Could not find poetry version in vendor.txt"
    vendor_version = poetry_line_match.group(1)

    # Check that the versions match
    assert pyproject_version == vendor_version, (
        f"Version mismatch: {pyproject_version} in pyproject.toml vs {vendor_version} in vendor.txt"
    )
