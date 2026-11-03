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
downloaded artifacts. A missing shard can hide a single test, since
pytest-split balances the shards by runtime, so the refresh runs on a scratch
copy of this directory and `scripts/install_refreshed_durations.py` copies the
result back while keeping every committed entry whose test still exists in the
source tree. Only the entries of removed tests are dropped. If a whole file is
missing after the refresh, that script fails the weekly workflow instead; check
the run before merging anything.

The filenames must match `<os>-py<version>.json` for every combination in the
test matrix. When a combination is removed from the matrix, delete its file by
hand, since the script above refuses to delete it. A matrix entry without a
file splits evenly by test count until the next refresh.

For more details, see the
[pytest-split documentation](https://jerry-git.github.io/pytest-split/).
