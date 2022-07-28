# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import absolute_import, division, print_function, unicode_literals

from logging import getLogger
import os
import sys

from .main import main as main_main
from .. import CondaError
from ..auxlib.ish import dals

log = getLogger(__name__)


def pip_installed_post_parse_hook(args, p):
    if args.cmd not in ('init', 'info'):
        raise CondaError(dals("""
        Conda has not been initialized.

        To enable full conda functionality, please run 'conda init'.
        For additional information, see 'conda init --help'.

        """))


def main(*args, **kwargs):
    os.environ[str('CONDA_PIP_UNINITIALIZED')] = str('true')
    kwargs['post_parse_hook'] = pip_installed_post_parse_hook
    return main_main(*args, **kwargs)


if __name__ == '__main__':
    sys.exit(main())
