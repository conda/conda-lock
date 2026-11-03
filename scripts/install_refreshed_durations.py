"""Install refreshed pytest-split timings only when the refresh looks complete.

The weekly update workflow runs conda/actions/combine-durations on a scratch
copy of tests/durations. That action deletes every file and every test entry
that is absent from the artifacts it downloaded, so an empty or partial
download would silently erase valid timings. This script copies the refreshed
files over the committed ones only when every committed file is still present
and none of them lost more than a small share of its entries.
"""

import argparse
import json
import shutil
import sys

from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refreshed",
        type=Path,
        required=True,
        help="directory that the refresh wrote to",
    )
    parser.add_argument(
        "--committed",
        type=Path,
        required=True,
        help="directory with the committed timings, updated in place",
    )
    parser.add_argument(
        "--max-loss",
        type=float,
        default=0.05,
        help="largest fraction of a file's entries that may disappear "
        "(default: %(default)s)",
    )
    args = parser.parse_args()

    problems: list[str] = []
    for committed in sorted(args.committed.glob("*.json")):
        refreshed = args.refreshed / committed.name
        if not refreshed.is_file():
            problems.append(f"{committed.name}: missing after the refresh")
            continue
        old = set(json.loads(committed.read_text()))
        new = set(json.loads(refreshed.read_text()))
        lost = len(old - new)
        if old and lost / len(old) > args.max_loss:
            problems.append(
                f"{committed.name}: {lost} of {len(old)} entries disappeared "
                f"(limit {args.max_loss:.0%})"
            )
    if problems:
        print(
            "Keeping the committed durations; "
            "the artifact download was probably incomplete:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    for refreshed in sorted(args.refreshed.glob("*.json")):
        shutil.copyfile(refreshed, args.committed / refreshed.name)
        print(f"Installed {refreshed.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
