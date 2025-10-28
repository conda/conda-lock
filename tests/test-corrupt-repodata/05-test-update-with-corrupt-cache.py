"""Test that --update with corrupt cache propagates corruption to lockfile.

This script demonstrates that the corruption bug manifests during --update operations
when conda-lock reads from a corrupt package cache created by buggy micromamba versions.

For more context, see:
<https://github.com/mamba-org/mamba/issues/4052>
"""

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

from pathlib import Path
from typing import Final


SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
DOCKERFILE_PATH: Final[Path] = SCRIPT_DIR / "04.Dockerfile"
SOURCE_FILE: Final[Path] = SCRIPT_DIR / "../../environments/dev-environment.yaml"
DEFAULT_MICROMAMBA_VERSION: Final[str] = "2.1.0"


def build_conda_lock_image(_: str = DEFAULT_MICROMAMBA_VERSION) -> str:
    """Deprecated: Docker build no longer used. Kept for CLI compatibility."""
    return ""


def test_update_with_cache(
    base_lockfile: Path,
    pkgs_dir: Path,
    update_package: str,
    output_name: str,
) -> Path:
    """Test conda-lock --update with a specific pkgs cache.

    Args:
        image_tag: Docker image tag to use
        base_lockfile: Path to the base lockfile
        pkgs_dir: Path to the pkgs directory to mount as cache
        update_package: Package to update
        output_name: Name for the output lockfile (without extension)

    Returns:
        Path to the generated lockfile
    """
    output_file = SCRIPT_DIR / f"{output_name}.yml"

    print("Testing --update with:")
    print(f"  Base lockfile: {base_lockfile}")
    print(f"  Pkgs cache:    {pkgs_dir}")
    print(f"  Update:        {update_package}")
    print(f"  Output:        {output_file}")
    print()

    # Run conda-lock locally using a temporary copy of the pkgs cache
    with tempfile.TemporaryDirectory(prefix="conda-lock-update-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        tmp_pkgs = tmpdir_path / "pkgs"
        tmp_pkgs.mkdir(parents=True, exist_ok=True)

        # Copy pkgs cache to a temp location to avoid any accidental writes to repo
        # Modern Python: use dirs_exist_ok to safely copy into an existing directory
        shutil.copytree(pkgs_dir, tmp_pkgs, symlinks=True, dirs_exist_ok=True)

        tmp_lockfile = tmpdir_path / "lockfile.yml"
        shutil.copy2(base_lockfile, tmp_lockfile)

        env = os.environ.copy()
        env["CONDA_PKGS_DIRS"] = str(tmp_pkgs)

        cmd = [
            "micromamba",
            "run",
            "--name=conda-lock-dev",
            "python",
            "-m",
            "conda_lock.conda_lock",
            "lock",
            "--log-level=DEBUG",
            f"--file={SOURCE_FILE}",
            "--platform=linux-64",
            f"--lockfile={tmp_lockfile}",
            f"--update={update_package}",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(SCRIPT_DIR.parent.parent),  # Run from repo root
        )

        if result.returncode != 0:
            print("STDERR:")
            print(result.stderr)
            print("STDOUT:")
            print(result.stdout)
            raise subprocess.CalledProcessError(
                result.returncode, result.args, result.stdout, result.stderr
            )

        shutil.copy2(tmp_lockfile, output_file)

    print(f"Generated: {output_file}")
    print()
    return output_file


def check_for_corruption(lockfile: Path) -> dict[str, list[str]]:
    """Check a lockfile for signs of corruption.

    Args:
        lockfile: Path to the lockfile to check

    Returns:
        Dict with corruption indicators:
        - 'empty_depends': list of packages with no dependencies
        - 'missing_sha256': list of packages missing sha256
    """
    import yaml

    with open(lockfile) as f:
        data = yaml.safe_load(f)

    empty_depends = []
    missing_sha256 = []

    for package in data.get("package", []):
        if package.get("manager") != "conda":
            continue

        # Check for empty depends (but allow packages that genuinely have no deps)
        depends = package.get("dependencies", {})
        if isinstance(depends, dict) and len(depends) == 0:
            # This might be suspicious, but some packages genuinely have no deps
            pass

        # Check for missing sha256 (this is suspicious for non-cache entries)
        hash_info = package.get("hash", {})
        if hash_info.get("md5") and not hash_info.get("sha256"):
            missing_sha256.append(package["name"])

    return {
        "empty_depends": empty_depends,
        "missing_sha256": missing_sha256,
    }


def main() -> None:
    """Test conda-lock --update with corrupt cache."""
    parser = argparse.ArgumentParser(
        description="Test conda-lock --update with corrupt package cache"
    )
    parser.add_argument(
        "--micromamba-version",
        default=DEFAULT_MICROMAMBA_VERSION,
        help=f"Micromamba version for base image (default: {DEFAULT_MICROMAMBA_VERSION})",
    )
    parser.add_argument(
        "--base-lockfile",
        default="lockfile-2.1.0-pkgs.yml",
        help="Base lockfile to update from (default: lockfile-2.1.0-pkgs.yml)",
    )
    parser.add_argument(
        "--update-package",
        default="pytest",
        help="Package to update (default: pytest)",
    )
    args = parser.parse_args()

    # Docker no longer required for this test
    _ = build_conda_lock_image(args.micromamba_version)

    # Paths
    base_lockfile = SCRIPT_DIR / args.base_lockfile
    if not base_lockfile.exists():
        print(f"Error: Base lockfile not found: {base_lockfile}", file=sys.stderr)
        print(
            "Run: python 04-test-conda-lock-with-pkgs.py --pkgs-archive 2.1.0-pkgs.tar.gz",
            file=sys.stderr,
        )
        sys.exit(1)

    good_pkgs_archive = SCRIPT_DIR / "2.1.0-pkgs.tar.gz"
    corrupt_pkgs_archive = SCRIPT_DIR / "2.1.1-pkgs.tar.gz"

    for pkgs_archive in [good_pkgs_archive, corrupt_pkgs_archive]:
        if not pkgs_archive.exists():
            version = pkgs_archive.stem.split("-")[0]  # Extract version from filename
            print(f"Error: pkgs archive not found: {pkgs_archive}", file=sys.stderr)
            print(
                f"Run: python 02-reproduce-corrupt-repodata-via-upstream.py --version={version}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Extract archives to persistent directories (for later inspection)
    good_pkgs = SCRIPT_DIR / "2.1.0-pkgs"
    corrupt_pkgs = SCRIPT_DIR / "2.1.1-pkgs"

    # Always delete and re-extract to ensure fresh state
    if good_pkgs.exists():
        if not good_pkgs.name.endswith("-pkgs"):
            raise ValueError(
                f"Safety check failed: refusing to delete {good_pkgs} (does not end with '-pkgs')"
            )
        shutil.rmtree(good_pkgs)
    print(f"Extracting {good_pkgs_archive}...")
    with tarfile.open(good_pkgs_archive, "r:gz") as tf:
        tf.extractall(path=SCRIPT_DIR)

    if corrupt_pkgs.exists():
        if not corrupt_pkgs.name.endswith("-pkgs"):
            raise ValueError(
                f"Safety check failed: refusing to delete {corrupt_pkgs} (does not end with '-pkgs')"
            )
        shutil.rmtree(corrupt_pkgs)
    print(f"Extracting {corrupt_pkgs_archive}...")
    with tarfile.open(corrupt_pkgs_archive, "r:gz") as tf:
        tf.extractall(path=SCRIPT_DIR)
    print()

    print("=" * 80)
    print("TEST 1: Update with GOOD cache (2.1.0-pkgs)")
    print("=" * 80)
    print()

    lockfile_good = test_update_with_cache(
        base_lockfile,
        good_pkgs,
        args.update_package,
        f"updated-with-good-cache-{args.update_package}",
    )

    print("=" * 80)
    print("TEST 2: Update with CORRUPT cache (2.1.1-pkgs)")
    print("=" * 80)
    print()

    lockfile_corrupt = test_update_with_cache(
        base_lockfile,
        corrupt_pkgs,
        args.update_package,
        f"updated-with-corrupt-cache-{args.update_package}",
    )

    print("=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    print()

    print("Checking for corruption indicators...")
    print()

    good_check = check_for_corruption(lockfile_good)
    corrupt_check = check_for_corruption(lockfile_corrupt)

    print(f"Good cache lockfile ({lockfile_good.name}):")
    print(f"  Packages missing sha256: {len(good_check['missing_sha256'])}")
    print()

    print(f"Corrupt cache lockfile ({lockfile_corrupt.name}):")
    print(f"  Packages missing sha256: {len(corrupt_check['missing_sha256'])}")
    print()

    if len(corrupt_check["missing_sha256"]) > len(good_check["missing_sha256"]):
        print("⚠️  CORRUPTION DETECTED!")
        print(
            f"   The corrupt cache resulted in {len(corrupt_check['missing_sha256']) - len(good_check['missing_sha256'])} more packages missing sha256"
        )
        print()
        print("Additional packages missing sha256 in corrupt version:")
        for pkg in set(corrupt_check["missing_sha256"]) - set(
            good_check["missing_sha256"]
        ):
            print(f"  - {pkg}")
    else:
        print("✓  No additional corruption detected")
        print("   (This might mean the update didn't trigger the reconstruction path)")

    print()
    print("To compare lockfiles:")
    print(f"  diff {lockfile_good.name} {lockfile_corrupt.name}")
    print()
    print("To see detailed differences:")
    print(f"  diff -u {lockfile_good.name} {lockfile_corrupt.name} | less")


if __name__ == "__main__":
    main()
