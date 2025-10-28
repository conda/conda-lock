"""Test conda-lock lockfile generation with different pkgs directories.

This script builds a Docker image with conda-lock installed, extracts the
specified pkgs archive, and generates lockfiles to compare the results.

The pkgs archives contain only metadata files (index.json and repodata_record.json)
from package cache directories. To reproduce the corruption issue, the container will:
1. Warm the default package cache via an explicit install from /explicit.lock
2. Copy the incomplete pkgs directory (metadata only) to a writable location
3. Configure micromamba to use the custom pkgs directory first
4. Run conda-lock, which reads corrupt metadata from the custom pkgs and uses warmed files

For more context, see:
<https://github.com/mamba-org/mamba/issues/4052>
"""

import argparse
import shlex
import shutil
import subprocess
import sys
import tarfile

from pathlib import Path
from typing import Final


SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
REPO_ROOT: Final[Path] = SCRIPT_DIR / "../.."
DOCKERFILE_PATH: Final[Path] = SCRIPT_DIR / "04.Dockerfile"
SOURCE_FILE: Final[Path] = SCRIPT_DIR / "../../environments/dev-environment.yaml"
EXPLICIT_LOCK: Final[Path] = SCRIPT_DIR / "01-explicit.lock"
DEFAULT_MICROMAMBA_VERSION: Final[str] = "2.1.0"
DEFAULT_PKGS_ARCHIVE: Final[str] = "2.1.0-pkgs.tar.gz"


def build_conda_lock_image(micromamba_version: str = DEFAULT_MICROMAMBA_VERSION) -> str:
    """Build Docker image with conda-lock installed.

    Args:
        micromamba_version: The micromamba version to use as base

    Returns:
        The Docker image tag
    """
    image_tag = f"conda-lock-test:{micromamba_version}"

    cmd = [
        "docker",
        "build",
        f"--file={DOCKERFILE_PATH}",
        f"--build-arg=MICROMAMBA_TAG={micromamba_version}",
        f"--tag={image_tag}",
        str(SCRIPT_DIR),
    ]

    print(f"Building Docker image: {image_tag}")
    print("Running command:")
    print(f"  {shlex.join(cmd)}")
    print()

    subprocess.run(cmd, check=True)

    return image_tag


def remove_container_if_exists(container_name: str) -> None:
    """Remove an existing container with the given name, if present.

    This avoids errors from docker run when a prior container with the same
    name was left behind (e.g., from an interrupted run).
    """
    subprocess.run(
        ["docker", "rm", "--force", container_name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def generate_lockfile_with_pkgs(
    image_tag: str,
    pkgs_dir: Path,
    output_name: str,
) -> Path:
    """Generate a conda-lock lockfile using a specific pkgs directory.

    The conda-lock source from the repo is mounted and installed in editable mode
    at runtime, so changes to the source code are immediately reflected.

    Args:
        image_tag: Docker image tag to use
        pkgs_dir: Path to the pkgs directory to mount as cache
        output_name: Name for the output lockfile (without extension)

    Returns:
        Path to the generated lockfile
    """
    output_file = SCRIPT_DIR / f"{output_name}.yml"

    print(f"Generating lockfile: {output_file}")
    print(f"Using pkgs directory: {pkgs_dir}")
    print("Using editable install from local source")
    print()

    # Build docker run command - mount custom pkgs directory and source
    # The pkgs directory is mounted as read-only and will be copied to a writable location
    # The run-conda-lock.sh script will:
    # 1. Copy /custom-pkgs-ro to ~/custom-pkgs-writeable
    # 2. Configure micromamba to use ~/custom-pkgs-writeable
    # 3. Run explicit install to populate missing package files
    # 4. Run conda-lock which reads from the now-populated corrupt cache
    # We can't use --rm because we need to copy the file out after the container exits.
    container_name = f"conda-lock-temp-{output_file.stem}"

    # Ensure any prior container is removed to avoid name conflicts
    remove_container_if_exists(container_name)

    cmd = [
        "docker",
        "run",
        "--interactive",
        "--tty",
        f"--name={container_name}",
        f"--volume={SOURCE_FILE}:/workspace/dev-environment.yaml:ro",
        f"--volume={pkgs_dir}:/custom-pkgs-ro:ro",
        f"--volume={EXPLICIT_LOCK}:/explicit.lock:ro",
        f"--volume={REPO_ROOT}:/conda-lock-src:ro",
        f"--volume={SCRIPT_DIR / '04-setup-editable.sh'}:/setup-editable.sh:ro",
        f"--volume={SCRIPT_DIR / '04-run-conda-lock.sh'}:/run-conda-lock.sh:ro",
        image_tag,
        "bash",
        "/run-conda-lock.sh",
    ]

    print("Running command:")
    print(f"  {shlex.join(cmd)}")
    print()
    try:
        subprocess.run(cmd, check=True)

        # Copy the lockfile out of the container
        print("Copying lockfile from container...")
        subprocess.run(
            ["docker", "cp", f"{container_name}:/tmp/lockfile.yml", str(output_file)],
            check=True,
        )
    finally:
        # Clean up the container
        remove_container_if_exists(container_name)

    print(f"Generated: {output_file}")
    print()
    return output_file


def main() -> None:
    """Test conda-lock with different pkgs directories."""
    parser = argparse.ArgumentParser(
        description="Test conda-lock lockfile generation with different pkgs directories"
    )
    parser.add_argument(
        "--micromamba-version",
        default=DEFAULT_MICROMAMBA_VERSION,
        help=f"Micromamba version for base image (default: {DEFAULT_MICROMAMBA_VERSION})",
    )
    parser.add_argument(
        "--pkgs-archive",
        dest="pkgs_archive_name",
        default=DEFAULT_PKGS_ARCHIVE,
        help=f"Name of pkgs archive to use (default: {DEFAULT_PKGS_ARCHIVE})",
    )
    parser.add_argument(
        "--output-name",
        help="Name for output lockfile (default: lockfile-{pkgs_name})",
    )
    args = parser.parse_args()

    # Build the Docker image
    image_tag = build_conda_lock_image(args.micromamba_version)

    # Determine paths
    pkgs_archive = SCRIPT_DIR / args.pkgs_archive_name

    # Check if archive exists
    if not pkgs_archive.exists():
        print(
            f"Error: Archive not found: {pkgs_archive}",
            file=sys.stderr,
        )
        # Try to extract version from filename like "2.1.0-pkgs.tar.gz"
        archive_stem = pkgs_archive.stem.replace(".tar", "")  # Remove .tar from .tar.gz
        version = archive_stem.split("-")[0] if "-" in archive_stem else "VERSION"
        print(
            f"Run: python 02-reproduce-corrupt-repodata-via-upstream.py --version={version}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine extraction directory name (remove .tar.gz suffix)
    pkgs_name = pkgs_archive.stem.replace(
        ".tar", ""
    )  # e.g., "2.1.0-pkgs.tar.gz" -> "2.1.0-pkgs"
    pkgs_dir = SCRIPT_DIR / pkgs_name

    # Always delete and re-extract to ensure fresh state
    if pkgs_dir.exists():
        if not pkgs_dir.name.endswith("-pkgs"):
            raise ValueError(
                f"Safety check failed: refusing to delete {pkgs_dir} (does not end with '-pkgs')"
            )
        shutil.rmtree(pkgs_dir)
    print(f"Extracting {pkgs_archive}...")
    with tarfile.open(pkgs_archive, "r:gz") as tf:
        tf.extractall(path=SCRIPT_DIR)
    print()

    output_name = args.output_name or f"lockfile-{pkgs_name}"
    lockfile = generate_lockfile_with_pkgs(image_tag, pkgs_dir, output_name)

    print("=" * 60)
    print("Success!")
    print(f"Generated lockfile: {lockfile}")
    print()
    print("To compare lockfiles, run:")
    print("  diff lockfile-2.1.0-pkgs.yml lockfile-2.1.1-pkgs.yml")
    print("  diff lockfile-2.1.0-pkgs.yml lockfile-2.3.3-pkgs.yml")


if __name__ == "__main__":
    main()
