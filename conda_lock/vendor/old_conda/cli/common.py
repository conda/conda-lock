# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import absolute_import, division, print_function, unicode_literals

from logging import getLogger
from os.path import basename, dirname, isdir, isfile, join
import re
import sys

from ..auxlib.ish import dals
from ..base.constants import ROOT_ENV_NAME
from ..base.context import context
from ..common.constants import NULL
from ..common.io import swallow_broken_pipe
from ..common.path import paths_equal
from ..common.serialize import json_dump
from ..models.match_spec import MatchSpec
from ..exceptions import EnvironmentLocationNotFound, DirectoryNotACondaEnvironmentError

def confirm(message="Proceed", choices=('yes', 'no'), default='yes'):
    assert default in choices, default
    if context.dry_run:
        from ..exceptions import DryRunExit
        raise DryRunExit()

    options = []
    for option in choices:
        if option == default:
            options.append('[%s]' % option[0])
        else:
            options.append(option[0])
    message = "%s (%s)? " % (message, '/'.join(options))
    choices = {alt: choice
               for choice in choices
               for alt in [choice, choice[0]]}
    choices[''] = default
    while True:
        # raw_input has a bug and prints to stderr, not desirable
        sys.stdout.write(message)
        sys.stdout.flush()
        user_choice = sys.stdin.readline().strip().lower()
        if user_choice not in choices:
            print("Invalid choice: %s" % user_choice)
        else:
            sys.stdout.write("\n")
            sys.stdout.flush()
            return choices[user_choice]


def confirm_yn(message="Proceed", default='yes', dry_run=NULL):
    dry_run = context.dry_run if dry_run is NULL else dry_run
    if dry_run:
        from ..exceptions import DryRunExit
        raise DryRunExit()
    if context.always_yes:
        return True
    try:
        choice = confirm(message=message, choices=('yes', 'no'), default=default)
    except KeyboardInterrupt:  # pragma: no cover
        from ..exceptions import CondaSystemExit
        raise CondaSystemExit("\nOperation aborted.  Exiting.")
    if choice == 'no':
        from ..exceptions import CondaSystemExit
        raise CondaSystemExit("Exiting.")
    return True


def ensure_name_or_prefix(args, command):
    if not (args.name or args.prefix):
        from ..exceptions import CondaValueError
        raise CondaValueError('either -n NAME or -p PREFIX option required,\n'
                              'try "conda %s -h" for more details' % command)


def arg2spec(arg, json=False, update=False):
    try:
        spec = MatchSpec(arg)
    except:
        from ..exceptions import CondaValueError
        raise CondaValueError('invalid package specification: %s' % arg)

    name = spec.name
    if not spec._is_simple() and update:
        from ..exceptions import CondaValueError
        raise CondaValueError("""version specifications not allowed with 'update'; use
    conda update  %s%s  or
    conda install %s""" % (name, ' ' * (len(arg) - len(name)), arg))

    return str(spec)


def specs_from_args(args, json=False):
    return [arg2spec(arg, json=json) for arg in args]


spec_pat = re.compile(r'(?P<name>[^=<>!\s]+)'  # package name  # lgtm [py/regex/unmatchable-dollar]
                      r'\s*'  # ignore spaces
                      r'('
                      r'(?P<cc>=[^=]+(=[^=]+)?)'  # conda constraint
                      r'|'
                      r'(?P<pc>(?:[=!]=|[><]=?|~=).+)'  # new (pip-style) constraint(s)
                      r')?$',
                      re.VERBOSE)  # lgtm [py/regex/unmatchable-dollar]


def strip_comment(line):
    return line.split('#')[0].rstrip()


def spec_from_line(line):
    m = spec_pat.match(strip_comment(line))
    if m is None:
        return None
    name, cc, pc = (m.group('name').lower(), m.group('cc'), m.group('pc'))
    if cc:
        return name + cc.replace('=', ' ')
    elif pc:
        if pc.startswith('~= '):
            assert pc.count('~=') == 1,\
                "Overly complex 'Compatible release' spec not handled {}".format(line)
            assert pc.count('.'), "No '.' in 'Compatible release' version {}".format(line)
            ver = pc.replace('~= ', '')
            ver2 = '.'.join(ver.split('.')[:-1]) + '.*'
            return name + ' >=' + ver + ',==' + ver2
        else:
            return name + ' ' + pc.replace(' ', '')
    else:
        return name


def specs_from_url(url, json=False):
    from ..gateways.connection.download import TmpDownload

    explicit = False
    with TmpDownload(url, verbose=False) as path:
        specs = []
        try:
            for line in open(path):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line == '@EXPLICIT':
                    explicit = True
                if explicit:
                    specs.append(line)
                    continue
                spec = spec_from_line(line)
                if spec is None:
                    from ..exceptions import CondaValueError
                    raise CondaValueError("could not parse '%s' in: %s" %
                                          (line, url))
                specs.append(spec)
        except IOError as e:
            from ..exceptions import CondaFileIOError
            raise CondaFileIOError(path, e)
    return specs


def names_in_specs(names, specs):
    return any(spec.split()[0] in names for spec in specs)


def disp_features(features):
    if features:
        return '[%s]' % ' '.join(features)
    else:
        return ''


@swallow_broken_pipe
def stdout_json(d):
    getLogger("conda.stdout").info(json_dump(d))


def stdout_json_success(success=True, **kwargs):
    result = {'success': success}
    actions = kwargs.pop('actions', None)
    if actions:
        if 'LINK' in actions:
            actions['LINK'] = [prec.dist_fields_dump() for prec in actions['LINK']]
        if 'UNLINK' in actions:
            actions['UNLINK'] = [prec.dist_fields_dump() for prec in actions['UNLINK']]
        result['actions'] = actions
    result.update(kwargs)
    stdout_json(result)


def print_envs_list(known_conda_prefixes, output=True):

    if output:
        print("# conda environments:")
        print("#")

    def disp_env(prefix):
        fmt = '%-20s  %s  %s'
        default = '*' if prefix == context.default_prefix else ' '
        if prefix == context.root_prefix:
            name = ROOT_ENV_NAME
        elif any(paths_equal(envs_dir, dirname(prefix)) for envs_dir in context.envs_dirs):
            name = basename(prefix)
        else:
            name = ''
        if output:
            print(fmt % (name, default, prefix))

    for prefix in known_conda_prefixes:
        disp_env(prefix)

    if output:
        print('')


def check_non_admin():
    from ..common._os import is_admin
    if not context.non_admin_enabled and not is_admin():
        from ..exceptions import OperationNotAllowed
        raise OperationNotAllowed(dals("""
            The create, install, update, and remove operations have been disabled
            on your system for non-privileged users.
        """))

def validate_prefix(prefix):
    """Verifies the prefix is a valid conda environment.

    :raises EnvironmentLocationNotFound: Non-existent path or not a directory.
    :raises DirectoryNotACondaEnvironmentError: Directory is not a conda environment.
    :returns: Valid prefix.
    :rtype: str
    """
    if isdir(prefix):
        if not isfile(join(prefix, 'conda-meta', 'history')):
            raise DirectoryNotACondaEnvironmentError(prefix)
    else:
        raise EnvironmentLocationNotFound(prefix)

    return prefix
