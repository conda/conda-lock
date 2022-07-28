# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import os

from .common import stdout_json
from ..base.context import context
from ..common.compat import text_type
from ..core.prefix_data import PrefixData
from ..gateways.connection.session import CONDA_SESSION_SCHEMES
from ..gateways.disk.test import is_conda_environment
from ..auxlib.path import expand
from conda_env import exceptions, specs
from ..models.match_spec import MatchSpec

log = logging.getLogger(__name__)

def get_packages(prefix):
    if not os.path.isdir(prefix):
        from ..exceptions import EnvironmentLocationNotFound
        raise EnvironmentLocationNotFound(prefix)

    return sorted(PrefixData(prefix, pip_interop_enabled=True).iter_records(),
                  key=lambda x: x.name)

def _get_name_tuple(pkg):
    return pkg.name, pkg

def _to_str(pkg):
    return "%s==%s=%s" % (pkg.name, pkg.version, pkg.build)

def compare_packages(active_pkgs, specification_pkgs):
    output = []
    res = 0
    ok = True
    for pkg in specification_pkgs:
        pkg_spec = MatchSpec(pkg)
        name = pkg_spec.name
        if name in active_pkgs:
            if not pkg_spec.match(active_pkgs[name]):
                ok = False
                output.append("{} found but mismatch. Specification pkg: {}, Running pkg: {}"
                              .format(name, pkg, _to_str(active_pkgs[name])))
        else:
            ok = False
            output.append("{} not found".format(name))
    if ok:
        output.append("Success. All the packages in the \
specification file are present in the environment \
with matching version and build string.")
    else:
        res = 1
    return res, output

def execute(args, parser):
    prefix = context.target_prefix
    if not is_conda_environment(prefix):
        from ..exceptions import EnvironmentLocationNotFound
        raise EnvironmentLocationNotFound(prefix)

    try:
        url_scheme = args.file.split("://", 1)[0]
        if url_scheme in CONDA_SESSION_SCHEMES:
            filename = args.file
        else:
            filename = expand(args.file)

        spec = specs.detect(name=args.name, filename=filename, directory=os.getcwd())
        env = spec.environment

        if args.prefix is None and args.name is None:
            args.name = env.name
    except exceptions.SpecNotFound:
        raise

    active_pkgs = dict(map(_get_name_tuple, get_packages(prefix)))
    specification_pkgs = []
    if 'conda' in env.dependencies:
        specification_pkgs = specification_pkgs + env.dependencies['conda']
    if 'pip' in env.dependencies:
        specification_pkgs = specification_pkgs + env.dependencies['pip']

    exitcode, output = compare_packages(active_pkgs, specification_pkgs)

    if context.json:
        stdout_json(output)
    else:
        print('\n'.join(map(text_type, output)))

    return exitcode
