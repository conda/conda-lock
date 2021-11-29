#!/usr/bin/env python

from distutils.core import setup


setup(
    name="test-package",
    version="0.1",
    packages=["testy"],
    install_requires=["requests"],
)
