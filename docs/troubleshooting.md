# Troubleshooting

## Metadata consistency errors

These errors mean conda-lock could not verify the package information needed to
create or update a lockfile. They do not necessarily mean your old lockfile is
corrupt: a channel may have changed a package's dependencies or removed a package
since you locked it. Cached package information can also be damaged or incomplete.

If the channel query itself failed, first check connectivity, authentication and
the solver's offline settings; that failure does not diagnose cache corruption.

Fresh locks and updates verify the selected Conda artifacts against channel
metadata before assigning categories and writing the result. This adopts channel
corrections as well as repairing damaged dependency lists. Package identities
and hashes must match; verification does not replace a selected artifact with a
different one. Solver-selected pip is retained, while its dependency metadata
is refreshed by the same rules as other packages.

During an update, refreshed requirements may expose a missing or incompatible
package. Conda-lock includes it and its affected dependents in the initial solver
request, preserving your source constraints. An unsatisfiable request fails;
there is no automatic full-regeneration retry. If a successful solve still
produces an inconsistent result, it is rejected with recovery and bug-report
instructions.

During `--update`, a successful channel query may show that a previously locked
artifact is no longer listed. Conda-lock warns and asks the solver to select a
replacement under your source requirements. For example, `--update runtime` can
also replace `analysis-tool` if its locked artifact has been removed. This applies
even if the old archive is still downloadable. Review the resulting lockfile.

An explicit source requirement such as `analysis-tool ==1.0` still applies. If no
available solution satisfies it, the update fails; conda-lock does not relax your
requirement. Review the solver's explanation and adjust source requirements only
if appropriate. Packages removed from your source files need no replacement.

Query failures and conflicting identities or hashes remain errors. If an error
says a newly selected artifact's channel metadata is unavailable, retry with
access to its channel; the channel may have changed during the update. Clearing
the package cache cannot restore a removed channel record. Installing an existing
lockfile is unchanged by this update behavior.

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

### Repeated metadata verification

Within one solve for one platform, conda-lock reuses channel records already
verified for the exact artifact. It still checks each supplied identity and hash;
new artifacts need another batched query. This avoids repeatedly loading the
same metadata during update preparation, reconstruction, and final validation.

These records are discarded after the operation. A later command verifies again.
The solver may also cache its channel index. Verification therefore establishes
consistency with observed channel metadata, not an atomic snapshot of a channel
that may change during the command. It does not rewrite damaged shared package
caches or change existing lockfile installation.

### Metadata query fallbacks

If conda-lock reports that your solver cannot batch metadata verification, use a
solver with multi-package `repoquery search` support. For Conda, this requires
`conda-libmamba-solver` 24.11.0 or newer to be installed and its plugin enabled in
the installation selected by `--conda`. Having a `repoquery` command alone is not
enough: older plugins accept only one package name per search.

To keep using an installation without batching, set `CONDA_LOCK_METADATA_FALLBACK`
for the command you are running:

| Value | Behavior when batching is unavailable |
| --- | --- |
| `error` (default) | Stop and explain the alternatives. |
| `unbatched` | Verify all required records with individual searches. This can take minutes or hours for large environments. |
| `skip` | Skip channel verification and refresh; retain available lockfile and package-cache metadata, with a warning. |

For example, in a POSIX shell:

```shell
CONDA_LOCK_METADATA_FALLBACK=unbatched conda-lock lock --update pytest
```

In PowerShell, set `$env:CONDA_LOCK_METADATA_FALLBACK = 'unbatched'` before running
the command and remove it afterwards with `Remove-Item Env:CONDA_LOCK_METADATA_FALLBACK`.
Choose `skip` only if you accept that missing dependencies or constraints may be
carried forward undetected. In that mode, automatic pruning of old packages is
disabled; the solver's own removals are still honored. Record validation,
identity checks against the solver plan, and graph integrity checks remain
active. If an update leaves packages with no category, it still fails: regenerate
from the source files instead of using `--update`.

Batching is always used when available, even with `skip` or `unbatched` set.
These fallbacks do not bypass failed channel queries, verification of selected
artifacts, identity mismatches or offline restrictions on a solver that supports batching.
An empty value is treated as unset (`error`); other unknown values are errors.
Installing an existing lockfile is unchanged.

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

## Pip solves

If an older Mamba reports an unsatisfiable Python/pip conflict even though you did
not request pip, try Conda:

```shell
conda-lock --conda conda --no-mamba --no-micromamba \
  -f environment.yml --lockfile recovered.conda-lock.yml
```

Mamba versions affected by [mamba#4422](https://github.com/mamba-org/mamba/issues/4422)
can inject pip despite conda-lock disabling it. Conda-lock preserves the solver's
selected packages, including extra pip, and refreshes dependency metadata from
the channel like other packages. It cannot repair a
solve that fails before returning a package plan. A Mamba release containing the
upstream fix is another option.

If the solver selects pip without connecting it to any requested package,
conda-lock includes pip and its dependencies in Python's categories. This keeps
the selected packages in the written lockfile. Pip already connected to the
requested dependency graph uses the categories of that graph. This exception
does not establish that the metadata is complete; other unreachable packages
and missing required dependencies still fail validation.

## Unsupported offline verification

Generating or updating lockfiles from cached remote-channel metadata in solver
offline mode is not a supported workflow. If the error says **"Offline Mamba
verification of remote channel metadata is not supported"**, conda-lock stopped
before querying the channel. Mamba's offline query can return extracted package
records, which cannot independently verify the metadata being checked.

Remove `--offline` from `CONDA_FLAGS` and check the selected solver's `offline`
setting:

```shell
mamba config list offline --sources
# Use micromamba instead of mamba when that is your selected solver.
```

Remove or disable the offline setting in the reported configuration source or in
`MAMBA_OFFLINE` / `CONDA_OFFLINE`, then retry. Merely having a populated cache
does not put conda-lock into offline mode.

Full verification does not make offline locking supported. Explicitly skipping
verification on a solver without batching can still carry damaged metadata into
a lockfile. Reading a local `file://` channel with its own repodata is separate
from reusing a remote channel's package cache.

## Suspicious package metadata

Conda-lock warns when it uses a solver-provided LINK record, or an unverified
cache record in skip mode, with both a zero timestamp and an empty license.
Older Mamba versions wrote damaged cache records
with this pattern, but valid packages can also have these values. The warning
does not repair the metadata or prove it is corrupt; other forms of damage may
go undetected.

Keep your existing lockfile. Before using the new result, regenerate online with
a current solver and an isolated cache, following the recovery steps above.
Compare the replacement, including its dependency categories. If the warning
persists with a clean cache, check the affected packages' channel metadata;
missing license and timestamp fields can be legitimate.
