# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import absolute_import, division, print_function, unicode_literals

from errno import EINVAL, EXDEV, EPERM
from logging import getLogger
import os
from os.path import dirname, isdir, split, basename, join, exists
import re
from shutil import move
from subprocess import Popen, PIPE

from . import exp_backoff_fn, mkdir_p, mkdir_p_sudo_safe
from .delete import rm_rf
from .link import lexists
from ...base.context import context
from ...common.compat import on_win
from ...common.path import expand
from ...exceptions import NotWritableError

log = getLogger(__name__)

SHEBANG_REGEX = re.compile(br'^(#!((?:\\ |[^ \n\r])+)(.*))')


class CancelOperation(Exception):
    pass


def update_file_in_place_as_binary(file_full_path, callback):
    # callback should be a callable that takes one positional argument, which is the
    #   content of the file before updating
    # this method updates the file in-place, without releasing the file lock
    fh = None
    try:
        fh = exp_backoff_fn(open, file_full_path, 'rb+')
        log.trace("in-place update path locked for %s", file_full_path)
        data = fh.read()
        fh.seek(0)
        try:
            fh.write(callback(data))
            fh.truncate()
            return True
        except CancelOperation:
            pass  # NOQA
    finally:
        if fh:
            fh.close()
    return False


def rename(source_path, destination_path, force=False):
    if lexists(destination_path) and force:
        rm_rf(destination_path)
    if lexists(source_path):
        log.trace("renaming %s => %s", source_path, destination_path)
        try:
            os.rename(source_path, destination_path)
        except EnvironmentError as e:
            if (on_win and dirname(source_path) == dirname(destination_path)
                    and os.path.isfile(source_path)):
                condabin_dir = join(context.conda_prefix, "condabin")
                rename_script = join(condabin_dir, 'rename_tmp.bat')
                if exists(rename_script):
                    _dirname, _src_fn = split(source_path)
                    _dest_fn = basename(destination_path)
                    p = Popen(['cmd.exe', '/C', rename_script, _dirname,
                               _src_fn, _dest_fn], stdout=PIPE, stderr=PIPE)
                    stdout, stderr = p.communicate()
                else:
                    log.debug("{} is missing.  Conda was not installed correctly or has been "
                              "corrupted.  Please file an issue on the conda github repo."
                              .format(rename_script))
            elif e.errno in (EINVAL, EXDEV, EPERM):
                # https://github.com/conda/conda/issues/6811
                # https://github.com/conda/conda/issues/6711
                log.trace("Could not rename %s => %s due to errno [%s]. Falling back"
                          " to copy/unlink", source_path, destination_path, e.errno)
                # https://github.com/moby/moby/issues/25409#issuecomment-238537855
                # shutil.move() falls back to copy+unlink
                move(source_path, destination_path)
            else:
                raise
    else:
        log.trace("cannot rename; source path does not exist '%s'", source_path)


def backoff_rename(source_path, destination_path, force=False):
    exp_backoff_fn(rename, source_path, destination_path, force)


def touch(path, mkdir=False, sudo_safe=False):
    # sudo_safe: use any time `path` is within the user's home directory
    # returns:
    #   True if the file did not exist but was created
    #   False if the file already existed
    # raises: NotWritableError, which is also an OSError having attached errno
    try:
        path = expand(path)
        log.trace("touching path %s", path)
        if lexists(path):
            os.utime(path, None)
            return True
        else:
            dirpath = dirname(path)
            if not isdir(dirpath) and mkdir:
                if sudo_safe:
                    mkdir_p_sudo_safe(dirpath)
                else:
                    mkdir_p(dirpath)
            else:
                assert isdir(dirname(path))
            with open(path, 'a'):
                pass
            # This chown call causes a false positive PermissionError to be
            # raised (similar to #7109) when called in an environment which
            # comes from sudo -u.
            #
            # if sudo_safe and not on_win and os.environ.get('SUDO_UID') is not None:
            #     uid = int(os.environ['SUDO_UID'])
            #     gid = int(os.environ.get('SUDO_GID', -1))
            #     log.trace("chowning %s:%s %s", uid, gid, path)
            #     os.chown(path, uid, gid)
            return False
    except (IOError, OSError) as e:
        raise NotWritableError(path, e.errno, caused_by=e)
