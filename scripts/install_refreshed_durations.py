"""Install refreshed pytest-split timings without losing the timings of live tests.

The weekly update workflow runs conda/actions/combine-durations on a scratch
copy of tests/durations. That action deletes every file and every test entry
that is absent from the artifacts it downloaded, so a shard whose artifact is
missing would silently erase the timings of its tests. Since pytest-split
balances the shards by runtime, a shard can consist of a single slow test, so
the loss is not visible in the number of entries. This script copies the
refreshed timings over the committed ones, keeping every committed entry whose
test still exists in the source tree. Only the entries of removed tests are
dropped.
"""

import argparse
import json
import re
import sys

from pathlib import Path


def test_exists(nodeid: str, root: Path) -> bool:
    """Whether the test of a pytest node id is still in the source tree.

    Node ids look like ``tests/test_x.py::test_y[param]`` for tests and
    ``conda_lock/x.py::conda_lock.x.y`` for doctests.
    """
    path, _, rest = nodeid.partition("::")
    source = root / path
    if not source.is_file():
        return False
    name = rest.split("[", 1)[0].split("::")[-1].split(".")[-1]
    if name == source.stem:
        # The doctest of a module docstring.
        return True
    pattern = rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b"
    source_text = source.read_text(encoding="utf-8")
    return re.search(pattern, source_text, re.MULTILINE) is not None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
        "--root",
        type=Path,
        default=Path(),
        help="directory that the node ids are relative to "
        "(default: the working directory)",
    )
    args = parser.parse_args()

    missing = [
        committed.name
        for committed in sorted(args.committed.glob("*.json"))
        if not (args.refreshed / committed.name).is_file()
    ]
    if missing:
        print(
            "Keeping the committed durations; the refresh produced no file for:",
            file=sys.stderr,
        )
        for name in missing:
            print(f"  {name}", file=sys.stderr)
        return 1

    for refreshed in sorted(args.refreshed.glob("*.json")):
        committed = args.committed / refreshed.name
        durations = json.loads(refreshed.read_text(encoding="utf-8"))
        kept = 0
        if committed.is_file():
            for nodeid, duration in json.loads(
                committed.read_text(encoding="utf-8")
            ).items():
                if nodeid not in durations and test_exists(nodeid, args.root):
                    durations[nodeid] = duration
                    kept += 1
        removed = [nodeid for nodeid in durations if not test_exists(nodeid, args.root)]
        for nodeid in removed:
            del durations[nodeid]
        committed.write_text(
            json.dumps(durations, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            f"Installed {refreshed.name}: {len(durations)} entries, "
            f"{kept} kept from the committed file, "
            f"{len(removed)} of removed tests dropped"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
