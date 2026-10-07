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

Before updating, conda-lock looks up the existing conda packages in their original
channels and verifies their artifact URLs and hashes. It refreshes their dependency
metadata before asking the solver to update the selected packages. Consequently,
dependency information can change even for packages whose versions stay the same:
channels can patch dependencies without changing the package archive.

This lookup uses the solver's channel metadata API and its configured repodata
cache. It requires metadata for the original artifacts to remain available.
If an artifact is missing or its identity cannot be verified, the update stops;
this does not by itself mean the old lockfile is corrupt.

Offline Mamba can include extracted package-cache records in channel queries,
so conda-lock cannot use those results to independently verify a remote channel.
Consequently, offline `--update` with Mamba is unsupported for remote channels.
Use conda with cached channel repodata for offline verification, or allow Mamba
to query the channel online. Local `file://` channels are read directly without
network access, including when the solve itself is offline.

Fresh offline locks do not require an extra query merely because pip is present.
They can still require verification when reconstructing sparse cached metadata,
before removing unsolicited pip, or when checking a possible injected Python-to-pip
dependency. The same offline Mamba limitation applies in those cases; see
[pip support](pip.md).

When channel repodata is unavailable, an offline Mamba solve can instead use
extracted records from a configured flat package cache. Missing dependency edges
in those records can go undetected if another requested package still brings in
the dependency. The lock can then assign that dependency to the wrong categories
and omit it from a category-specific installation. The graph checks do not
guarantee complete dependency metadata in this case. To verify the result,
regenerate online from the original sources with an isolated cache, including
checking any additional configured `pkgs_dirs`.

### Recovering from a metadata consistency error

Conda-lock checks the supplied dependency graph for missing required packages,
unsatisfied package constraints, and packages without a category. These checks
cannot detect every missing dependency edge. An error can indicate damaged cached
metadata, unavailable channel records, changed channel dependencies, or an
incomplete older lockfile.

To regenerate, use your original source files and a current solver with a fresh
package cache. Check the solver's configured `pkgs_dirs` as well: additional cache
directories may still expose old records. Keep the old lockfile while generating
the replacement, and do not pass `--update` or `--check-input-hash`:

```shell
conda-lock -f environment.yml -p osx-64 -p linux-64 --lockfile recovered.conda-lock.yml
```

Retain the platforms, categories and virtual-package settings of your original
command. Review the replacement before using it; regeneration can select newer
packages. Selective updates can repair missing dependency edges, but a solver may
leave an already-missing package absent during a no-op update. Conda-lock refuses
that incomplete result, so regeneration is still needed in that case.

Verified unrequested pip additions are removed before category validation, while
explicit and genuine pip dependencies retain their categories. Unexplained
packages with no category still cause an error; they are never silently assigned
to the main category. See [pip support](pip.md) for the migration behavior.

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
