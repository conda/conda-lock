#!/usr/bin/env python3

"""Reproduce the corrupt repodata record by running micromamba from a Docker image.

Writes the repodata records to the pkgs/ directory.

Last good version: 2.1.0
First bad version: 2.1.1
Last bad version: 2.3.2
First good version: 2.3.3

For more context, see:
<https://github.com/mamba-org/mamba/issues/4052>
"""

import argparse
import gzip
import io
import shlex
import subprocess
import tarfile

from pathlib import Path
from typing import Final


SCRIPT_DIR: Final[Path] = Path(__file__).resolve().parent
DOCKERFILE_PATH: Final[Path] = SCRIPT_DIR / "02.Dockerfile"
EXPLICIT_LOCK_PATH: Final[Path] = SCRIPT_DIR / "01-explicit.lock"
SOURCE_DIR: Final[str] = "/opt/conda/pkgs"
DEFAULT_VERSION: Final[str] = "2.1.1"


def build_docker_image(version: str = DEFAULT_VERSION) -> None:
    """Build the Docker image that produces a (potentially) corrupt repodata record.

    The build uses the Dockerfile and tags the image for later use.

    Args:
        version: The micromamba version to use (e.g., "2.1.1")
    """
    image_tag = f"bad-repodata:{version}"

    cmd = [
        "docker",
        "build",
        f"--file={DOCKERFILE_PATH}",
        f"--build-arg=MICROMAMBA_TAG={version}",
        f"--tag={image_tag}",
        str(SCRIPT_DIR),
    ]

    print("Running command:")
    print(f"  {shlex.join(cmd)}")
    print()

    subprocess.run(cmd, check=True)


def extract_pkgs_from_image(
    version: str = DEFAULT_VERSION, destination: Path | None = None
) -> Path:
    """Create a container from the built image and save the pkgs directory as tar.gz.

    Writes the compressed archive next to this script unless a destination is provided.

    Args:
        version: The micromamba version to use (e.g., "2.1.1")
        destination: Optional destination path for the tar.gz file

    Returns:
        Path to the created tar.gz file
    """
    image_tag = f"bad-repodata:{version}"

    # Create a container without starting it
    result = subprocess.run(
        ["docker", "create", image_tag],
        check=True,
        capture_output=True,
        text=True,
    )
    container_id = result.stdout.strip()

    try:
        # Get the archive of the pkgs directory
        result = subprocess.run(
            ["docker", "cp", f"{container_id}:{SOURCE_DIR}", "-"],
            check=True,
            capture_output=True,
        )
        tar_bytes = result.stdout

        output_file = destination or SCRIPT_DIR / f"{version}-pkgs.tar.gz"

        # Filter and recompress the tar archive
        # Docker gives us a tar, we need to filter it and save as tar.gz
        # Include a parent directory so extraction creates only one directory
        parent_dir = f"{version}-pkgs"

        # Collect members and their data for reproducible output
        members_to_add = []
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tf_in:
            for member in tf_in.getmembers():
                # Only include index.json and repodata_record.json from info/ directories
                if not (
                    member.name.endswith("/info/index.json")
                    or member.name.endswith("/info/repodata_record.json")
                ):
                    continue

                # Strip "pkgs/" prefix and add parent directory
                name = member.name
                if name.startswith("pkgs/"):
                    name = name[5:]  # Remove "pkgs/"
                name = f"{parent_dir}/{name}"

                # Store name and file data
                fileobj = tf_in.extractfile(member)
                if fileobj:
                    members_to_add.append((name, fileobj.read()))

        # Sort by name for reproducible ordering
        members_to_add.sort(key=lambda x: x[0])

        # Write sorted members to a deterministic gzip-compressed tar
        out_bytes = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=out_bytes, mtime=0) as gz:
            with tarfile.open(
                fileobj=gz, mode="w", format=tarfile.PAX_FORMAT
            ) as tf_out:
                for name, data in members_to_add:
                    # Create TarInfo with reproducible metadata
                    ti = tarfile.TarInfo(name=name)
                    ti.size = len(data)
                    ti.mtime = 0
                    ti.uid = 0
                    ti.gid = 0
                    ti.uname = ""
                    ti.gname = ""
                    ti.mode = 0o644
                    tf_out.addfile(ti, io.BytesIO(data))

        # Persist to disk
        with open(output_file, "wb") as f_out:
            f_out.write(out_bytes.getvalue())

        return output_file
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container_id],
            check=True,
        )


def cleanup_docker_image(version: str = DEFAULT_VERSION) -> None:
    """Remove the Docker image created by build_docker_image().

    Args:
        version: The micromamba version to use (e.g., "2.1.1")
    """
    image_tag = f"bad-repodata:{version}"
    subprocess.run(
        ["docker", "rmi", "--force", image_tag],
        check=True,
    )


def main(version: str = DEFAULT_VERSION) -> None:
    """Build the Docker image and extract the corrupt repodata records.

    Args:
        version: The micromamba version to use (e.g., "2.1.1")
    """
    print(f"Building Docker image: bad-repodata:{version}")
    build_docker_image(version)

    print(f"Extracting pkgs directory to: {SCRIPT_DIR / f'{version}-pkgs.tar.gz'}")
    output_path = extract_pkgs_from_image(version)

    print(f"Successfully wrote pkgs archive to: {output_path}")
    print("Cleaning up Docker image...")
    cleanup_docker_image(version)
    print("Done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Reproduce corrupt repodata records from micromamba Docker images"
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"Micromamba version to use (default: {DEFAULT_VERSION})",
    )
    args = parser.parse_args()
    main(version=args.version)
