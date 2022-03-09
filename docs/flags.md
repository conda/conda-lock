# Options

## --conda

If you wish to specify a particular conda/mamba executable to use to perform the solve this can be provided as follows

```bash
conda-lock --conda some_path_to_conda
```

---

## --mamba

If you want to make use of mamba in order to perform faster solves this can be enabledd with

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

When generating lockfiles that make use of extras it is recommended to make use of `--filename-template` covered [here](#file-naming).

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

Conda makes use of [virtual packages](https://conda.io/projects/conda/en/latest/user-guide/tasks/manage-virtual.html) that are available at
runtime to gate dependency on system features.  Due to these not generally existing on your local execution platform conda-lock will inject
them into the solution environment with a reasonable guess at what a default system configuration should be.

If you want to override which virtual packages are injected you can create a virtual package spec file

```{.yaml title="virtual-packages.yml"}
subdirs:
  linux-64:
    packages:
      __glibc: 2.17
      __cuda: 11.4
  win-64:
    packages:
      __cuda: 11.4
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
