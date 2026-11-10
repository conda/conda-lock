"""Print one channel-qualified CI dependency spec per line from pyproject.toml."""

import tomllib

from pathlib import Path

from packaging.requirements import Requirement


def main() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as f:
        requirements = {
            requirement.name: requirement
            for requirement in map(
                Requirement, tomllib.load(f)["project"]["dependencies"]
            )
        }

    # These packages need newer bounds than the released conda-forge recipe.
    # Their PyPI and Conda names match; qualification preserves channel priority.
    specs = []
    for name in ("dulwich", "filelock", "virtualenv"):
        requirement = requirements[name]
        if requirement.url or requirement.extras or requirement.marker:
            raise ValueError(
                f"Cannot translate requirement to a Conda spec: {requirement}"
            )
        specs.append(f"conda-forge::{name}{requirement.specifier}")
    print("\n".join(specs))


if __name__ == "__main__":
    main()
