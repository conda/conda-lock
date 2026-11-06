# Options

## --conda

If you wish to specify a particular conda/mamba executable to use to perform the solve this can be provided as follows

```bash
conda-lock --conda some_path_to_conda
```

---

## --mamba

If you want to make use of mamba in order to perform faster solves this can be enabled with

```
conda-lock --mamba some_path_to_conda
```

If mamba is not installed, it will attempt to install it using [ensureconda](https://github.com/conda-incubator/ensureconda)

---

## --filename-template

By default, `conda-lock` store its output in `conda-lock.yml` in the current
working directory. This file will also be used by default for render, install,
and update operations. You can supply a different filename with e.g.

```bash
conda-lock --lockfile superspecial.conda-lock.yml
```

The extension `.conda-lock.yml` will be added if not present. Rendered
environment files (env or explicit) will be named as as
`"conda-{platform}.lock"`.

If you want to override that call conda-lock as follows.

```bash
conda-lock -k explicit --filename-template "specific-{platform}.conda.lock"
```

The following fields are available for templating

| field             |                                                           |
| ----------------- | --------------------------------------------------------- |
| platform          | the platform for lock file (conda subdir)                 |
| dev-dependencies  | true/false flag for --dev-dependencies                    |
| input-hash        | a sha256 hash of the lock file input specification        |
| version           | the version of conda-lock used                            |
| timestamp         | the timestamp of the output file in ISO8601 basic format  |

---

## --channel

You can override the channels that are used by conda-lock in case you need to override the ones specified in
an [environment.yml][envyaml] or any of the other supported formats.

```bash
conda-lock --channel conda-forge
```

---

## --platform

You may specify the platforms you wish to target by default directly in an [environment.yml][envyaml]

If you specify target platforms on the command line with `--platform`, these will
override the values in the environment specification. If neither `platforms` (from source files) nor
`--platforms` are provided, `conda-lock` will fall back to a default set of platforms.

---

## --dev-dependencies/--no-dev-dependencies

By default conda-lock will include dev dependencies in the specification of the lock (if the files that the lock
is being built from support them).  This can be disabled easily

```bash
conda-lock --no-dev-dependencies --file ./recipe/meta.yaml
```

---

## --extras or --categories

If your source files contains optional dependencies/extras these can be included in the output of a `render` by using the
flag.

```sh
conda-lock --extra mysql --extra pgsql -f pyproject.toml
```

When generating lockfiles that make use of extras it is recommended to make use of `--filename-template` covered [here](#-filename-template).

!!! note ""

    By default conda-lock will attempt to solve for *ALL* extras/categories it discovers in sources.  This allows you to _render_ or _install_
    from lockfiles of extras without needing to re-lock.

    However this does make the assumption that *all* extras are installed, *and* installable in conjunction with each other.
    If you want extras filtering to happen at the before solving use the flag `--filter-categories` or `--filter-extras`

    ```sh
    conda-lock --extra incompatiblea --filter-categories -f pyproject.toml
    ```

    This will use categories from `--extras/--categories` flag as a filter at the specification build time.

---

## --update

Update named packages in an existing lockfile to the latest versions compatible
with the source specification. This applies to both Conda and pip dependencies.
Repeat the option to name more than one package:

```shell
conda-lock -f environment.yml --update numpy --update pandas
```

Each `--update` requires a **package name**, not a version constraint. Put version
constraints in the source file, for example `numpy >=2,<3` in `environment.yml`,
then pass `--update numpy`. For pip packages, use the normalized name stored in
the lockfile (lowercase, with underscores and periods replaced by hyphens).
Passing `--update` without a value is a command-line error.

Selective updates do not guarantee that every other package stays unchanged:

- Conda and micromamba may change other packages to satisfy the solver request
  and their configuration. In particular, conda-lock does not disable Conda's
  `aggressive_update_packages` setting.
- With a `mamba` executable, conda-lock pins the versions of non-target Conda
  packages. These pins do not fix builds or channels and can make an update
  unsatisfiable. Try Conda or micromamba if those pins prevent a solution.
- Pip resolution also considers the locked packages, but may change dependencies
  to satisfy the updated requirements.

!!! warning "Current selective-update limitations"

    Conda update targets are matched literally against package names already
    locked for each platform. If none match, the Conda dependencies are solved
    from scratch for that platform. A typo, a constraint such as `numpy==2.0`,
    or an update naming only pip packages can therefore update unrelated Conda
    packages. An installed transitive Conda target absent from the source
    specification can instead fail with `KeyError`.

    Changes to source constraints on other packages may not be applied during a
    selective update. Reusing an existing lockfile also rejects changes to its
    channel list. For source or channel changes that require a fresh resolution,
    write to a new lockfile path and review the result:

    ```shell
    conda-lock -f environment.yml --lockfile fresh-conda-lock.yml
    ```

    These limitations are tracked in [#824](https://github.com/conda/conda-lock/issues/824)
    and [#955](https://github.com/conda/conda-lock/issues/955).
    [#947](https://github.com/conda/conda-lock/pull/947) proposes changes to update
    handling. These descriptions record current behavior, not a guarantee that
    the limitations will be preserved.

Omitting `--update` re-solves the Conda dependencies, unless `--check-input-hash`
skips the solve. Existing pip locks can still be retained. Use a new lockfile path
as above to resolve both Conda and pip dependencies without the previous lock.

---

## --check-input-hash

Under some situation you may want to run conda lock in some kind of automated way (eg as a precommit) and want to not
need to regenerate the lockfiles if the underlying input specification for that particular lock as not changed.

```bash
conda-lock --check-input-hash --platform linux-64
```

When the input_hash of the input files, channels match those present in a given lockfile, that lockfile will not be regenerated.

---

{%
   include-markdown "./flags/strip-auth.md"
   heading-offset=1
%}

---

## --virtual-package-spec

Conda uses [virtual packages](https://conda.io/projects/conda/en/latest/user-guide/tasks/manage-virtual.html) to describe system-level
features like the glibc version, macOS version, or CUDA version.  Since conda-lock generates
lockfiles independently of a target system, it assumes a default set of virtual package versions
that represent a reasonable minimum system configuration.

Each virtual package version acts as an upper bound on which packages are considered by
the solver (packages requiring a newer version are excluded) and as a lower bound on
compatible target systems (the lockfile is installable on systems that meet or exceed
these versions).

There is a tradeoff: increasing a version widens the set of candidate packages but narrows
the set of compatible target systems.  If a package you need is being filtered out, increase the
relevant version.  If the lockfile is incompatible with your target system, decrease it.

To override the defaults, create a virtual package spec file

```{.yaml title="virtual-packages.yml"}
subdirs:
  linux-64:
    packages:
      __glibc: "2.17"
      __cuda: "11.4"
      __archspec: "1 haswell"
  win-64:
    packages:
      __cuda: "11.4"
      __archspec: "1 haswell"
```

conda-lock will automatically use a `virtual-packages.yml` it finds in the the current working directory.  Alternatively one can be specified
explicitly via the flag.

```bash
conda lock --virtual-package-spec virtual-packages-cuda.yml --platform linux-64
```

### Input hash stability

Virtual packages take part in the input hash so if you build an environment with a different set of virtual packages the input hash will change.
Additionally the default set of virtual packages may be augmented in future versions of conda-lock.  If you desire very stable input hashes
we recommend creating a `virtual-packages.yml` file to lock down the virtual packages considered.

!!! warning "in conjunction with micromamba"

    Micromamba does not presently support some of the overrides to remove all discovered virtual packages, consequently the set of virtual packages
    available at solve time may be larger than those specified in your specification.
