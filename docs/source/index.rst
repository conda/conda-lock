conda-lock
==========

Solve once, install multiple times
----------------------------------

Conda lock is a lightweight library that can be used to generate fully reproducible lock files for conda environments.

It does this by performing multiple solves for conda targeting a set of platforms you desire lockfiles for.

This also has the added benefit of acting as an external presolve for conda as the lockfiles it generates
results in the conda solver **not** being invoked when installing the packages from the generated lockfile.

Installation
------------

  .. code-block:: bash

     conda install --channel conda-forge conda-lock

Usage
-----

Generate the lockfiles,

  .. code-block:: bash

     conda-lock --file environment.yml linux-64

Create an environment from the lockfile

  .. code-block:: bash

     conda create --name my-locked-env --file conda-linux-64.lock
