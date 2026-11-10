#!/usr/bin/env python3

"""Clobber repodata_record.json files to simulate the corruption bug.

This script modifies repodata_record.json files from a good pkgs directory (2.1.0)
to match the corruption pattern seen in micromamba versions 2.1.1-2.3.2, where
metadata is lost when installing from explicit lockfiles.

After clobbering, it verifies that the result matches the corrupt version (2.1.1)
by running a diff and ensuring it's empty.

For more context, see:
<https://github.com/mamba-org/mamba/issues/4052>
"""

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tarfile

from pathlib import Path
from typing import Final


DEFAULT_INPUT_VERSION: Final[str] = "2.1.0"
DEFAULT_CORRUPT_VERSION: Final[str] = "2.1.1"


def extract_pkgs_archive(archive_path: Path, extract_to: Path) -> None:
    """Extract a pkgs tar.gz archive.

    Args:
        archive_path: Path to the .tar.gz file
        extract_to: Directory to extract into
    """
    extract_to.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tf:
        tf.extractall(path=extract_to)
    print(f"Extracted {archive_path} to {extract_to}")


def clobber_repodata_records(
    input_dir: Path, output_dir: Path, corruption_pattern: str = "2.1.1"
) -> None:
    """Clobber repodata_record.json files to simulate corruption.

    Args:
        input_dir: Source pkgs directory (e.g., "2.1.0-pkgs")
        output_dir: Destination directory for clobbered files (e.g., "clobbered-2.1.0-pkgs")
        corruption_pattern: Which corruption pattern to apply ("2.1.1" or "2.3.3")
    """
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    # Remove output directory if it exists
    if output_dir.exists():
        if not output_dir.name.endswith("-pkgs"):
            raise ValueError(
                f"Safety check failed: refusing to delete {output_dir} (does not end with '-pkgs')"
            )
        shutil.rmtree(output_dir)

    # Copy the entire directory structure
    shutil.copytree(input_dir, output_dir)

    # Find and modify all repodata_record.json files
    repodata_files = list(output_dir.rglob("*/info/repodata_record.json"))

    print(
        f"Found {len(repodata_files)} repodata_record.json files to clobber (pattern: {corruption_pattern})"
    )

    for repodata_file in repodata_files:
        # Read the original file
        with open(repodata_file, encoding="utf-8") as f:
            data = json.load(f)

        # Apply corruption pattern based on version
        if corruption_pattern == "2.1.1":
            # Versions 2.1.1-2.3.2: corrupt depends, constrains, license, timestamp, build_number, track_features
            data["depends"] = []
            data["constrains"] = []
            data["license"] = ""
            data["timestamp"] = 0
            data["build_number"] = 0
            data["track_features"] = ""
        elif corruption_pattern == "2.3.3":
            # Version 2.3.3: mirror presence/content of depends/constrains from info/index.json
            # and corrupt license/timestamp/build_number/track_features

            index_path = repodata_file.with_name("index.json")
            try:
                with open(index_path, encoding="utf-8") as f_idx:
                    index_data = json.load(f_idx)
            except FileNotFoundError:
                index_data = {}

            # depends: set exactly as in index.json (delete if absent)
            if "depends" in index_data:
                data["depends"] = index_data["depends"]
            elif "depends" in data:
                del data["depends"]

            # constrains: keep non-empty from index.json, otherwise remove
            if index_data.get("constrains"):
                data["constrains"] = index_data["constrains"]
            elif "constrains" in data:
                del data["constrains"]

            data["license"] = ""
            data["timestamp"] = 0
            data["build_number"] = 0
            data["track_features"] = ""
        else:
            raise ValueError(
                f"Unknown corruption pattern: {corruption_pattern}. Use '2.1.1' or '2.3.3'"
            )

        # Write back the modified data
        with open(repodata_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    print(f"Clobbered {len(repodata_files)} files in {output_dir}")


def verify_matches_corrupt(
    clobbered_dir: Path, corrupt_dir: Path, verbose: bool = False
) -> bool:
    """Verify that the clobbered directory matches the corrupt version.

    Args:
        clobbered_dir: The directory with clobbered files
        corrupt_dir: The directory with corrupt files from micromamba
        verbose: Whether to print diff output

    Returns:
        True if directories match (diff is empty), False otherwise
    """
    cmd = [
        "diff",
        "--recursive",
        "--brief",
        str(clobbered_dir),
        str(corrupt_dir),
    ]

    print("Verifying clobbered files match corrupt version...")
    print(f"Running command: {shlex.join(cmd)}")
    print()

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print("✓ SUCCESS: Clobbered files match corrupt version exactly!")
        return True
    else:
        print("✗ FAILURE: Clobbered files differ from corrupt version!")
        print()
        if verbose or result.stdout:
            print("Differences found:")
            print(result.stdout)
        if result.stderr:
            print("Errors:")
            print(result.stderr)
        return False


def main() -> None:
    """Clobber repodata_record.json files to simulate the corruption bug."""
    parser = argparse.ArgumentParser(
        description="Clobber repodata_record.json files to simulate corruption and verify"
    )
    parser.add_argument(
        "--input-version",
        default=DEFAULT_INPUT_VERSION,
        help=f"Version of the input pkgs directory (default: {DEFAULT_INPUT_VERSION})",
    )
    parser.add_argument(
        "--corrupt-version",
        default=DEFAULT_CORRUPT_VERSION,
        help=f"Version of the corrupt pkgs directory to compare against (default: {DEFAULT_CORRUPT_VERSION})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed diff output if verification fails",
    )
    parser.add_argument(
        "--pattern",
        default="2.1.1",
        choices=["2.1.1", "2.3.3"],
        help="Corruption pattern to apply: '2.1.1' (full corruption) or '2.3.3' (partial corruption, default: 2.1.1)",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    input_archive = script_dir / f"{args.input_version}-pkgs.tar.gz"
    corrupt_archive = script_dir / f"{args.corrupt_version}-pkgs.tar.gz"
    output_dir = script_dir / f"clobbered-{args.input_version}-pkgs"

    # Check that input archives exist
    if not input_archive.exists():
        print(f"Error: Input archive not found: {input_archive}", file=sys.stderr)
        print(
            f"Run: python 02-reproduce-corrupt-repodata-via-upstream.py --version={args.input_version}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not corrupt_archive.exists():
        print(f"Error: Corrupt archive not found: {corrupt_archive}", file=sys.stderr)
        print(
            f"Run: python 02-reproduce-corrupt-repodata-via-upstream.py --version={args.corrupt_version}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Extract archives to persistent directories (for later inspection)
    input_dir = script_dir / f"{args.input_version}-pkgs"
    corrupt_dir = script_dir / f"{args.corrupt_version}-pkgs"

    print(f"Input archive:     {input_archive}")
    print(f"Corrupt archive:   {corrupt_archive}")
    print(f"Output directory:  {output_dir}")
    print()

    # Always delete and re-extract to ensure fresh state
    if input_dir.exists():
        if not input_dir.name.endswith("-pkgs"):
            raise ValueError(
                f"Safety check failed: refusing to delete {input_dir} (does not end with '-pkgs')"
            )
        shutil.rmtree(input_dir)
    print(f"Extracting {input_archive}...")
    extract_pkgs_archive(input_archive, script_dir)

    if corrupt_dir.exists():
        if not corrupt_dir.name.endswith("-pkgs"):
            raise ValueError(
                f"Safety check failed: refusing to delete {corrupt_dir} (does not end with '-pkgs')"
            )
        shutil.rmtree(corrupt_dir)
    print(f"Extracting {corrupt_archive}...")
    extract_pkgs_archive(corrupt_archive, script_dir)
    print()

    clobber_repodata_records(input_dir, output_dir, corruption_pattern=args.pattern)
    print()

    # Verify the clobbered files match the corrupt version
    matches = verify_matches_corrupt(output_dir, corrupt_dir, verbose=args.verbose)
    print()

    if matches:
        print("✓ Verification passed!")
        sys.exit(0)
    else:
        print("✗ Verification failed!")
        print()
        print("To see detailed differences, run:")
        print(f"  diff --recursive {output_dir} {corrupt_dir}")
        sys.exit(1)


if __name__ == "__main__":
    main()
