# pytest-split durations

These files contain timings measured by our GitHub Actions test runs.
pytest-split uses them to balance the shards; tests without a timing use the
average duration.

After all shards pass, `test.yml` combines their timings into the
`aggregated-test-durations` artifact. The weekly `update.yml` workflow uses
`conda/actions/combine-durations` to average recent aggregates with the committed
timings and propose an update PR. Incomplete runs do not supply an aggregate.

The updater is rendered from `conda/infrastructure`. Its workflow name,
artifact pattern, and durations directory are configured in
`.github/template-files/config.yml`; edit that configuration rather than the
generated workflow.

Filenames must match `<os>-py<version>.json` in the test matrix. Update them when
changing Python versions so pytest-split can use existing timings immediately.

See the [pytest-split documentation](https://jerry-git.github.io/pytest-split/).
