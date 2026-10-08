# Troubleshooting

## Metadata consistency errors

These errors mean conda-lock could not verify the package information needed to
create or update a lockfile. They do not necessarily mean your old lockfile is
corrupt: a channel may have changed a package's dependencies or removed a package
since you locked it. Cached package information can also be damaged or incomplete.

Keep your existing lockfile. To generate a replacement, use the original source
files, a current solver, and an isolated package cache. Write to a new path, without
`--update` or `--check-input-hash`:

```shell
conda-lock -f environment.yml -p osx-64 -p linux-64 --lockfile recovered.conda-lock.yml
```

Use the platforms, categories and virtual-package settings from your original
command. Review the replacement before using it: regeneration may select newer
packages. An update can repair some dependency information, but may still fail
when the old lockfile lacks a required package; regenerate in that case.

### Identify additional package caches

Conda-lock already requests a temporary package cache. Your solver can merge that
setting with additional directories from its configuration. Those directories
may expose the same damaged records again, so simply retrying might not help.

Run the commands for the executable you pass to `conda-lock --conda`. These
commands inspect configuration without changing it:

```shell
# Mamba 2.x; replace mamba with micromamba when applicable
mamba config list pkgs_dirs --sources

# Conda (also appropriate for Python-based Mamba 1.x)
conda config --show pkgs_dirs
conda config --show-sources
```

`pkgs_dirs` lists the package caches the solver can read. The source output
identifies configuration files or environment variables that supply those paths.
For recovery, every additional cache should be empty or excluded from this run.
Back up the identified configuration files before temporarily changing their
`pkgs_dirs` entries to a new, empty directory. Check `MAMBA_PKGS_DIRS` as well if
it appears in the output. Re-run the inspection to confirm the old paths are
gone, regenerate, and restore your configuration afterwards. No cache deletion
is necessary.

### Isolate a Mamba recovery run

For public channels that do not need custom authentication, proxy, or certificate
settings, you can instead ignore configuration files for one run. The following
example uses a POSIX shell and micromamba:

```shell
env -u CONDARC -u MAMBARC -u MAMBA_PKGS_DIRS -u CONDA_FLAGS \
  MAMBA_NO_RC=true MAMBA_OFFLINE=false CONDA_OFFLINE=false \
  conda-lock --conda micromamba --micromamba \
  -f environment.yml -p linux-64 --lockfile recovered.conda-lock.yml
```

Conda-lock supplies the fresh cache, and these overrides prevent Mamba from
adding caches from configuration files or `MAMBA_PKGS_DIRS`. They also allow
online verification. Your shell settings, configuration files, caches and old
lockfile are unchanged. Adapt the source files and platforms to your project.
For channels requiring custom configuration, use the targeted configuration
changes above so those settings remain available.

## Pip and offline solves

If an older Mamba reports an unsatisfiable Python/pip conflict even though you did
not request pip, try Conda:

```shell
conda-lock --conda conda --no-mamba --no-micromamba \
  -f environment.yml --lockfile recovered.conda-lock.yml
```

Mamba versions affected by [mamba#4422](https://github.com/mamba-org/mamba/issues/4422)
can inject pip despite conda-lock disabling it. Conda-lock can remove verified
unrequested pip from a successful solve, but cannot repair a solve that fails
before returning a package plan. A Mamba release containing the upstream fix is
another option.

If the error says **"Offline Mamba queries may return extracted package-cache
metadata"**, either allow online access or use Conda with cached channel repodata.
For online access, remove `--offline` from `CONDA_FLAGS` and check the solver's
`offline` setting:

```shell
mamba config list offline --sources
# Or, for Conda:
conda config --show offline
conda config --show-sources
```

Remove or disable the offline setting in the reported configuration source or in
`MAMBA_OFFLINE` / `CONDA_OFFLINE`, then retry. Merely having a populated cache
does not put conda-lock into offline mode.

Offline Mamba cannot independently verify remote channel records when updating
an existing lockfile, recovering incomplete cached package information, or
checking whether pip can be omitted. Explicitly requesting pip does not itself
require this verification. Local `file://` channels can be verified without
network access.

### Limit of offline verification

An offline Mamba solve can succeed using extracted package records when cached
channel repodata is missing. If those records have lost dependency information,
conda-lock cannot detect every omission. For example, if a main dependency has
lost its requirement on a shared package, but a dev dependency still requires
that package, it can be assigned only to dev and left out of a main-only install.
The checks for missing packages, unsatisfied constraints and unassigned categories
do not catch that case. To check such a result, regenerate online with an isolated
cache using the steps above.
