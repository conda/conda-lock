# pytest-split durations

This directory contains test durations as they were computed by pytest-split
running from GitHub Actions.

The split is only as good as these timings, so they need to be refreshed.
(Tests that are missing from this list will be assigned the average duration.)

The JSON files are generated during the pytest runs on GitHub and uploaded as
`test-durations-<os>-py<version>-<group>` artifacts. The weekly
`update.yml` workflow downloads the artifacts from recent runs on `main`,
averages them with the committed files, and opens a pull request with the
result. The same files are also aggregated in the "Aggregate test durations"
job and available for download as the `aggregated-test-durations` artifact.

The refresh deletes every file and every test entry that is absent from the
downloaded artifacts, so it runs on a scratch copy of this directory, and
`scripts/install_refreshed_durations.py` copies the result back only if every
committed file is still present and none of them lost more than 5% of its
entries. If the weekly workflow fails at that step, the download was probably
incomplete; check the run before merging anything.

The filenames must match `<os>-py<version>.json` for every combination in the
test matrix. When a combination is removed from the matrix, delete its file by
hand, since the guard above stops the refresh from deleting it. A matrix entry
without a file splits evenly by test count until the next refresh.

For more details, see the
[pytest-split documentation](https://jerry-git.github.io/pytest-split/).
