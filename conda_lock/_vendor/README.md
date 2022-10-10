# Vendored dependencies

This directory contains an entire vendored copy of Conda so that we can make use of:

* conda.models.MatchSpec
* conda.common.toposort

It also contains parts of Poetry (and the associated Poetry Core and Cleo packages) which are used to solve for pip-related dependencies.

## Licenses

Further information about the vendored and subvendored licenses can be found in [LICENSES.md](LICENSES.md).
