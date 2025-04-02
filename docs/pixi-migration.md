# pixi migration guide

So you want to try pixi, the new project-centric Conda environment manager?
We will walk you through the steps to get started, whether or not you are a conda-lock user.

Perhaps "migration" is the wrong word, because pixi is an option, not a replacement.
It will not override existing workflows.
Removing pixi from your project is as simple as deleting the files indicated in step 2 below.

## Quick example

Use conda-lock and pixi to make a development environment for mypy.

```sh
# Install/upgrade conda-lock
pipx upgrade conda-lock || pipx install --force "conda-lock>=3"
cd /tmp
git clone https://github.com/python/mypy
cd mypy
git checkout -b add-pixi
echo .pixi >> .gitignore
conda-lock render-lock-spec --kind=pixi.toml --stdout \
    --file=pyproject.toml \
    --channel=conda-forge \
    --pixi-project-name=mypy \
    --editable mypy=. \
    > pixi.toml

# Solve the environment
pixi update
# Activate the environment
pixi shell
mypy --version

# Commit the changes
git add .gitignore
git commit -m "Ignore pixi cache directory"
git add pixi.toml
git commit -m "Add pixi.toml configuration"
git add pixi.lock
git commit -m "Add pixi.lock lock file"
```

## The steps to get started

1. Visit <https://pixi.sh> and install pixi if you haven't already.
    You can also find information about IDE integrations and other documentation.
    *Note*: As of writing, VS Code's Python extension has native support for pixi.

2. Prepare for the new files and directories related to pixi.

    - `pixi.toml` defines the environment specifications and config options. We will generate this with `conda-lock`.

    - `pixi.lock` is managed by pixi and maintains a particular solution to the environment specifications from `pixi.toml`.

    - `.pixi/` is the environment cache directory where the environments from the lock file `pixi.lock` are installed and updated.
        Pixi will recreate this directory if it is deleted.

    Both `pixi.toml` and `pixi.lock` should be version controlled. The `.pixi/` directory should be ignored by `.gitignore`, `.dockerignore`, and/or similar.

    *Note*: For Python projects it is possible to use `pyproject.toml` instead of `pixi.toml`.
    For starters we recommend sticking with `pixi.toml` since as of writing there is very little advantage to using `pyproject.toml`.
    Using separate files also provides more clear separation between Python project dependency management (`pyproject.toml`) and environment management (`pixi.toml`). Besides, migrating to `pyproject.toml` is mostly a matter of prefixing the table names in `pixi.toml` with `tool.pixi.`.

3. Install the latest version of `conda-lock` if you don't already have it.

    ```sh
    pipx install --force "conda-lock>=3"
    ```

    or

    ```sh
    pipx upgrade conda-lock
    ```

4. Generate a `pixi.toml` file with `conda-lock`.

    ```sh
    conda-lock render-lock-spec --kind=pixi.toml --stdout \
        --file=source-file-1 \
        --file=source-file-2 \
        ...
        --pixi-project-name=my-project-name
    ```

    Each `--file` argument should include a path to some `environment.yml` or `pyproject.toml`.

    The `--pixi-project-name` allows you to specify the name of the project.

    For Python development, you can optionally include one or more `--editable python-project-name=relative-path` arguments to specify local packages. For example,

    ```sh
    --editable python-package=.
    ```

    works like `pip install -e .`. (More generally, `.` can be replaced with the relative path to a Python project root containing a `pyproject.toml` or `setup.py` file.)

    For example for `conda-lock` we use the following command to generate our `pixi.toml` file:

    ```sh
    conda-lock render-lock-spec --kind=pixi.toml --stdout \
      --file=environments/dev-environment.yaml \
      --file=pyproject.toml \
      --pixi-project-name=conda-lock \
      --editable conda-lock=.
    ```

    When you are satisfied with the output, append `> pixi.toml` to the command to save it to a file.

    Pay attention to any warnings that `conda-lock` may print. Some specifications may not be translatable, and others might not yet be implemented.

    By default, dependencies from `pyproject.toml` will be converted to conda-forge dependencies. If you wish to keep them as PyPI dependencies, you can add the following table to the `pyproject.toml`:

    ```toml
    [tool.conda-lock]
    default-non-conda-source = "pip"
    ```

    If you have a `test-dependencies.yml` file and want it to define a feature named `test`, then you should add

    ```yaml
    category: test
    ```

    to the top level of `test-dependencies.yml`.

5. (Optional) Generate a `pixi.lock` file with pixi.

    ```sh
    pixi update
    ```

    This step is optional because pixi will automatically generate a lock file as needed from the other commands.
    By running `pixi update` we are doing one step at a time.

6. (Optional) Install the environment from the lock file.

    ```sh
    pixi install
    ```

    Optional because pixi automatically installs environments as needed.

7. Start an activated shell to test the environment.

    ```sh
    pixi shell
    ```

    This command replaces `conda activate`. However, it uses a subshell instead of the current shell, so instead of `conda deactivate` you use `exit`.

8. Commit `pixi.toml` and `pixi.lock` to your version control system.

## Concepts

### Features

Conda-lock has a concept called "categories" for grouping dependencies.
Each dependency belongs to one or more categories.
When conda-lock generates a lock file, it solves for an environment containing all categories and determines which dependencies are needed for each category.
An environment can be created by seleting some subset of categories, and only the necessary dependencies will be installed.

Pixi has a similar but more clever concept called "features".
Unlike categories, features are groups of dependency specifications that need not be mutually compatible.
Named environments are specified by selecting some mutually compatible features.
Then each named environment is solved for independently.

There is also a default environment, and it can be overridden.
For convenience, we override the default environment to include all categories.

#### The default feature and environment

Dependencies specified under `[dependencies]` or `[pypi-dependencies]` are grouped into the default feature.
All environments implicitly include the default feature.

In contrast, the `default` environment is simply the environment that is used when no environment is specified.
Unless otherwise specified, the `default` environment includes only the default feature.
But in practice, one often wants to default to a "batteries-included" environment.

## Infrastructure

### GitHub Actions

If you used [setup-micromamba](https://github.com/mamba-org/setup-micromamba), [setup-miniconda](https://github.com/conda-incubator/setup-miniconda) or similar, then for pixi you can use [setup-pixi](https://pixi.sh/latest/advanced/github_actions/).

For periodically updating the lock file, you can use [pixi-diff-to-markdown](https://pixi.sh/latest/advanced/updates_github_actions/).

### Docker

See [pixi-docker](https://github.com/prefix-dev/pixi-docker).

## Contributions welcome

We would be delighted to receive PRs to improve and extend both this guide and `conda-lock render-lock-spec`.
