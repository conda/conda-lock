from __future__ import annotations

import re
import subprocess

from itertools import chain

from migrate_code import Migration, get_repo_root

from conda_lock.scripts.vendor_poetry.vendor_helpers import (
    Requirement,
    get_directly_vendored_dependencies,
    get_vendor_namespace,
    get_vendor_root,
    merge_requirements,
    req_to_req_obj,
)


m = Migration("Vendor poetry")

directly_vendored_dependencies = get_directly_vendored_dependencies()


@m.add_stage(1, "Add vendored dependency requirements to conda-lock")
def add_vendored_requirements() -> None:
    # The list of requirements which we should add
    relevant_requirements: list[Requirement] = []

    # Iterate over cleo, poetry, and poetry-core
    for dep in directly_vendored_dependencies.values():
        # Get the list of requirements as a list of strings.
        # Typical string: ('cachecontrol[filecache] (>=0.12.9,<0.13.0); '
        #                  'python_version >= "3.6" and python_version < "4.0"')
        req_list = dep._sdist_obj.requires_dist

        for req_str in req_list:
            req: Requirement | None = req_to_req_obj(req_str, dep)
            if req is None:
                continue
            else:
                relevant_requirements.append(req)

    # Some of the requirements may occur multiple times, so we should merge them.
    merged_requirements: dict[str, Requirement] = merge_requirements(
        relevant_requirements
    )
    # Filter out the directly vendored dependencies
    filtered_requirements = {
        k: v
        for k, v in merged_requirements.items()
        if k not in directly_vendored_dependencies
    }

    # Update the requirements.txt file

    requirements_txt_file = get_repo_root() / "requirements.txt"
    requirements_txt = requirements_txt_file.read_text()

    # Remove some requirements which are redundant
    for line in ["poetry <1.2", "requests >=2"]:
        requirements_txt = requirements_txt.replace(line + "\n", "")
    filtered_requirements["requests"].sources.append("conda-lock")

    for requirement in filtered_requirements.values():
        # Construct the pair of lines to append to requirements.txt.
        # e.g. ('# poetry, poetry-core:\n'
        #       'importlib-metadata >=1.7.0,<2.0.0; python_version <= 3.7')
        requirement_line = requirement.as_requirements_txt_line()
        requirements_txt += requirement_line + "\n"

    requirements_txt_file.write_text(requirements_txt)


@m.add_stage(2, "Update pypi_solver.py to use vendored Poetry imports")
def modify_vendored_imports() -> None:
    for src_file in [get_repo_root() / "conda_lock" / "pypi_solver.py"]:
        src = src_file.read_text()
        # This is the main logic for updating
        for old, new in [
            ("poetry", f"{get_vendor_namespace()}.poetry"),
        ]:
            src = src.replace(f"import {old}", f"import {new}")
            src = src.replace(f"from {old}", f"from {new}")
        src_file.write_text(src)
    print("Run pre-commit to fix formatting. (Expected to show failing stages.)")
    subprocess.run(
        [
            "pre-commit",
            "run",
            "--files",
            get_repo_root() / "conda_lock" / "pypi_solver.py",
        ]
    )
    print("Pre-commit complete. Code should be fixed now.")


@m.add_stage(3, "Remove pexpect, requests_toolbelt, and shellingham as dependencies")
def remove_unnecessary_dependencies() -> None:
    to_remove = ["pexpect", "requests-toolbelt", "shellingham"]
    subprocess.check_output(["pipreqs", str(get_vendor_root() / "poetry")])
    poetry_requirements_txt = (
        get_vendor_root() / "poetry" / "requirements.txt"
    ).read_text()
    poetry_requirements: list[str] = []
    for line in poetry_requirements_txt.splitlines():
        pkg_name = line.split("==")[0].strip("- ").replace("_", "-")
        poetry_requirements.append(pkg_name)
    assert all(pkg_name not in poetry_requirements for pkg_name in to_remove)
    conda_lock_requirements_txt = (get_repo_root() / "requirements.txt").read_text()
    new_requirements_txt = ""
    for line in conda_lock_requirements_txt.splitlines():
        if any(line.startswith(f"{pkg_name} ") for pkg_name in to_remove):
            continue
        new_requirements_txt += line + "\n"
    (get_repo_root() / "requirements.txt").write_text(new_requirements_txt)
    (get_vendor_root() / "poetry" / "requirements.txt").unlink()


@m.add_stage(4, "Remove upper bounds on poetry dependencies")
def remove_upper_bounds() -> None:
    conda_lock_requirements_txt = (get_repo_root() / "requirements.txt").read_text()
    new_requirements = ""
    for line1, line2 in zip(
        conda_lock_requirements_txt.splitlines(),
        conda_lock_requirements_txt.splitlines()[1:],
    ):
        if ",<" in line2 and line1.startswith("# ") and "poetry" in line1:
            line2 = re.sub(r",<[0-9.]+", "", line2)
        if new_requirements == "":
            new_requirements = line1 + "\n"
        new_requirements += line2 + "\n"
    (get_repo_root() / "requirements.txt").write_text(new_requirements)


@m.add_stage(5, "Use 'vendoring sync' to vendor dependencies")
def vendor_dependencies() -> None:
    # Use the vendoring package to vendor the dependencies
    # https://pypi.org/project/vendoring/
    subprocess.check_output(["vendoring", "sync"], cwd=get_repo_root())


@m.add_stage(6, "Delete botched license file copies")
def delete_botched_license_files() -> None:
    vr = get_vendor_root()
    for license_file in chain(
        vr.glob("poetry_core.*LICENSE*"), vr.glob("poetry_core.*COPYING*")
    ):
        license_file.unlink()


@m.add_stage(7, "Recreate license files")
def add_poetry_root_licenses() -> None:
    """Add the root licenses for Poetry, Poetry Core, and Cleo.

    Copy them to "_vendor/packagename.LICENSE".
    They are all MIT licenses.
    This does not deal with the vendored dependencies of Poetry Core.
    """
    for dep_data in directly_vendored_dependencies.values():
        license = dep_data._root_license
        assert license.is_mit
        destination_dir = get_vendor_root()
        destination_dir.mkdir(parents=True, exist_ok=True)
        (destination_dir / f"{dep_data.name}.LICENSE").write_text(license.text)


@m.add_stage(8, "Describe vendored dependencies in conda-lock LICENSE")
def collect_poetry_core_vendored_dependencies() -> None:
    """Collect info about poetry-core's vendored dependencies.

    Also show that there aren't any vendored dependencies in poetry.

    (We only need to vendor a single file from cleo, so its vendored
    dependencies are not relevant.)
    """
    # The only vendored dependencies should exist in poetry-core.
    for dep_name, dep in directly_vendored_dependencies.items():
        dep.discovered_licenses = [dep._root_license]
        if dep_name == "poetry-core":
            continue
        discovered_vendored_dependencies = dep.search_vendored_dependencies()
        assert len(discovered_vendored_dependencies) == 0

    poetry_core = directly_vendored_dependencies["poetry-core"]
    discovered_vendored_dependencies = poetry_core.search_vendored_dependencies()

    conda_lock_license_file = get_repo_root() / "LICENSE"
    conda_lock_license = conda_lock_license_file.read_text()
    licenses_md = """\
# Vendored licenses

Conda lock vendors (and subvendors) several Python packages to reduce the number of dependencies.

## Conda

* conda, licensed as [BSD-3-Clause](conda.LICENSE.txt), Copyright (c) 2012, Anaconda, Inc.
  * auxlib, licensed as [ISC](conda/auxlib/LICENSE), Copyright (c) 2015, Kale Franz
  * boltons, licensed as [BSD-3-Clause](conda/_vendor/boltons/LICENSE), Copyright (c) 2013, Mahmoud Hashemi
  * pytoolz, licensed as [BSD-3-Clause](conda/_vendor/toolz/LICENSE.txt), Copyright (c) 2013 Matthew Rocklin
  * tqdm, licensed as [MIT](conda/_vendor/tqdm/LICENSE), Copyright (c) 2013 noamraph
  * urllib3, licensed as [MIT](conda/_vendor/urllib3/LICENSE.txt), Copyright 2008-2016 Andrey Petrov and contributors

## Poetry

"""
    conda_lock_license += "\n".join(
        [f"* {dep.describe_short()}" for dep in directly_vendored_dependencies.values()]
    )
    licenses_md += "\n".join(
        [
            f"* {dep.describe_markdown()}"
            for dep in directly_vendored_dependencies.values()
        ]
    )
    conda_lock_license += "\n"
    licenses_md += "\n"
    # Print subdependencies of poetry-core at the next level of indentation.
    assert list(directly_vendored_dependencies.keys())[-1] == "poetry-core"
    conda_lock_license += "\n".join(
        [
            f"  * {dep.describe_short()}"
            for dep in discovered_vendored_dependencies.values()
        ]
    )
    licenses_md += "\n".join(
        [
            f"  * {dep.describe_markdown()}"
            for dep in discovered_vendored_dependencies.values()
        ]
    )
    conda_lock_license += "\n"
    licenses_md += "\n"
    licenses_md_file = get_vendor_root() / "LICENSES.md"
    conda_lock_license += (
        f"\nFor more detailed information, please refer to "
        f"{licenses_md_file.relative_to(get_repo_root())}\n"
    )
    conda_lock_license_file.write_text(conda_lock_license)
    licenses_md_file.write_text(licenses_md)
