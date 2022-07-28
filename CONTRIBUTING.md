
## Developing

For the most up-to-date instructions see the github actions [test.yml workflow](./github/workflows.test.yml)

1. Ensure conda and mamba are installed. Install [mambaforge](https://github.com/conda-forge/miniforge#mambaforge) if you're otherwise not sure which one to pick.
2. `mamba create -n conda-lock-dev pip pytest-cov pytest-xdist pip`
3. `conda activate conda-lock-dev`
4. `python -m pip install -r requirements.txt`
5. `python -m pip install -r requirements-dev.txt`
6. `pip install -e . --no-deps --force-reinstall`

Run the tests to ensure that everything is running correctly. Due to the nature of this project, it hits remote webservers regularly so some tests occasionally fail. This is a normal part of conda-lock development. If you're not sure if your env is borked or the remote webserver is just being flaky, run the tests again. If you're still not sure you can open an issue about.

7. `pytest`

Whilst not strictly necessary; the CI run using github actions will run pre-commit in order to reduce development friction you may want to install the pre-commit hooks:

8. `pre-commit install`


# Keeping for historical reasons
## Moving to git submodules
All command blocks will start from the root of the conda_lock git repo

0. Move the old vendored conda code to "old_conda"
```
cd conda_lock/vendor
git mv conda old_comda
git commit -m  "Move old conda vendor to 'old_conda'"
```
1. Init the submodule
```
cd conda_lock/vendor
git submodule add https://github.com/conda/conda
```
2. Check out the commit where we initally copied the conda source from
```
cd conda_lock/vendor/conda
git checkout 2967d902d
# Then check out the conda-lock branch
git checkout -b conda-lock
```
3. Copy over the old vendored conda code and commit that
```
cd conda_lock/vendor
cp -r old_conda conda/conda
git commit -m "Committing previous set of copy/pasted conda code"
```
4. Add a __init__.py file in the root of the conda submodule
5. Fix up the conda imports from within conda_lock
```
from conda_lock.vendor.conda.<whatever>
```
needs to change to:
```
from conda_lock.vendor.conda.conda.<whatever>
```
