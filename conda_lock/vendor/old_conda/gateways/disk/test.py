# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import absolute_import, division, print_function, unicode_literals

from logging import getLogger
from os import W_OK, access
from os.path import basename, dirname, isdir, isfile, join
from uuid import uuid4

from .create import create_link
from .delete import rm_rf
from .link import islink, lexists
from ...auxlib.decorators import memoize
from ...base.constants import PREFIX_MAGIC_FILE
from ...common.compat import text_type
from ...common.path import expand
from ...models.enums import LinkType

log = getLogger(__name__)


def file_path_is_writable(path):
    path = expand(path)
    log.trace("checking path is writable %s", path)
    if isdir(dirname(path)):
        path_existed = lexists(path)
        try:
            fh = open(path, 'a+')
        except (IOError, OSError) as e:
            log.debug(e)
            return False
        else:
            fh.close()
            if not path_existed:
                rm_rf(path)
            return True
    else:
        # TODO: probably won't work well on Windows
        return access(path, W_OK)


@memoize
def hardlink_supported(source_file, dest_dir):
    test_file = join(dest_dir, '.tmp.%s.%s' % (basename(source_file), text_type(uuid4())[:8]))
    assert isfile(source_file), source_file
    assert isdir(dest_dir), dest_dir
    if lexists(test_file):
        rm_rf(test_file)
    assert not lexists(test_file), test_file
    try:
        # BeeGFS is a file system that does not support hard links between files in different
        # directories. Sometimes a soft link will be created with the hard link system call.
        create_link(source_file, test_file, LinkType.hardlink, force=True)
        is_supported = not islink(test_file)
        if is_supported:
            log.trace("hard link supported for %s => %s", source_file, dest_dir)
        else:
            log.trace("hard link IS NOT supported for %s => %s", source_file, dest_dir)
        return is_supported
    except (IOError, OSError):
        log.trace("hard link IS NOT supported for %s => %s", source_file, dest_dir)
        return False
    finally:
        rm_rf(test_file)


@memoize
def softlink_supported(source_file, dest_dir):
    # On Windows, softlink creation is restricted to Administrative users by default. It can
    # optionally be enabled for non-admin users through explicit registry modification.
    log.trace("checking soft link capability for %s => %s", source_file, dest_dir)
    test_path = join(dest_dir, '.tmp.' + basename(source_file))
    assert isfile(source_file), source_file
    assert isdir(dest_dir), dest_dir
    assert not lexists(test_path), test_path
    try:
        create_link(source_file, test_path, LinkType.softlink, force=True)
        return islink(test_path)
    except (IOError, OSError):
        return False
    finally:
        rm_rf(test_path)


def is_conda_environment(prefix):
    return isfile(join(prefix, PREFIX_MAGIC_FILE))
