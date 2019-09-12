# conda-lock

Conda lock is an experimental script that can be used to generate fully reproducible lock file for conda.

It does this by performing multiple solves for conda targeting a set of platforms you desire lockfiles for.

This also has the added benefit of acting as an external presolve for conda as the lockfiles it generates
results in the conda solver *not* being invoked when installing the solve.

## usage

```bash
# generate the lockfiles
./conda-lock.py -f environment.yml -p osx-64 -p linux-64

# create an environment from the lockfile
conda create -n mylockedenv --file conda-linux-64.lock
```

