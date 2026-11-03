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

The filenames must match `<os>-py<version>.json` for every combination in the
test matrix. A file that no longer matches the matrix is deleted by the
refresh, and a matrix entry without a file splits evenly by test count until
the next refresh.

For more details, see the
[pytest-split documentation](https://jerry-git.github.io/pytest-split/).
