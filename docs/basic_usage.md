# Basic Usage

Consider the following source specification:

```{.yaml title="environment.yml"}
channels:
  - conda-forge
dependencies:
  - python=3.12
  - numpy
```

### Generating a Lockfile

Generate a multi-platform lockfile `conda-lock.yml`

```shell
conda-lock -f environment.yml -p osx-64 -p linux-64
```

### Creating an Environment

Create an environment from the lockfile

```shell
conda-lock install [-p {prefix}|-n {name}]
```

Alternatively, render a single-platform lockfile and use conda command directly

```shell
conda-lock render -p linux-64
conda create -n my-locked-env --file conda-linux-64.lock
```

### Updating Packages

Update the previous solution, using the latest version of numpy that is
compatible with the source specification. This command overrides the lockfile.

```shell
conda-lock --update numpy
```

Updates also refresh the locked packages' requirements from channel metadata.
Channel corrections or removal of a previously locked artifact can require the
solver to change packages beyond the named update target. Review the resulting
lockfile; conda-lock does not automatically retry a failed update as a full
regeneration.

### Metadata verification and compatibility changes

Fresh locks and updates verify the selected Conda artifacts against their channel
records. Updates require access to the configured channels; offline updates from
remote package caches are unsupported. Installing an existing lockfile does not
perform this verification or change its packages.

- Mamba 1.x and Conda without working multi-package repoquery support stop by
  default. Upgrade the solver/plugin or explicitly choose the
  [metadata fallback](flags.md#conda_lock_metadata_fallback).
- Updated channel requirements can change dependency strings in an older
  lockfile even when its package versions stay the same. Metadata repair can
  also change packages not named in `--update`, while enforcing source pins.
- Removing a package's channel from the source can make its old artifact
  unavailable. Conda-lock then asks the solver for a replacement from the
  remaining channels, rather than carrying the old artifact forward.
- Missing dependencies, unsatisfied constraints and unexplained packages outside
  the source specification now cause errors instead of silent omissions.
  Configured `create_default_packages` additions are suppressed during fresh
  solves. Malformed optional `constrains` entries warn and are ignored, matching
  solver behavior; malformed required dependencies stop the operation.
- Solver-injected pip and its dependencies are retained. Fresh locks can contain
  more packages than before; see [pip support](pip.md).

An artifact replaced at the same URL with different checksums is an identity
conflict, not a missing artifact. After checking the channel or mirror, generate
a fresh lockfile from the source files **without `--update`** and review it. Do
not edit hashes to bypass verification. Conflicting channel records are rejected
even if one candidate agrees with the old lockfile.

Verification requires a matching artifact identity and at least one shared
checksum, and rejects any conflicting shared checksums. Additional checksums
from the lockfile or solver cache are preserved when the channel omits them;
those supplementary values are not independently reverified.

During updates, the temporary solver prefix contains expanded channel credentials
in its `conda-meta` records. The prefix is normally cleaned up automatically;
remove credentials before sharing preserved temporary directories for debugging.
Lockfiles retain their configured environment-variable placeholders.

### Adding new Packages

Add a new package to the environment

```{.yaml title="Updated environment.yml"}
channels:
  - conda-forge
dependencies:
  - python=3.12
  - numpy
  - pandas  # new
```

and regenerate the lockfile

```shell
conda-lock -f environment.yml -p osx-64 -p linux-64
```

Note that this updates existing packages.
