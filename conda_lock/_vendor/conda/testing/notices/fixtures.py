# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause

from pathlib import Path

from unittest import mock
import pytest

from conda_lock._vendor.conda.base.constants import NOTICES_CACHE_SUBDIR
from conda_lock._vendor.conda.cli import conda_argparse


@pytest.fixture(scope="function")
def notices_cache_dir(tmpdir):
    """
    Fixture that creates the notices cache dir while also mocking
    out a call to user_cache_dir.
    """
    with mock.patch("conda.notices.cache.user_cache_dir") as user_cache_dir:
        user_cache_dir.return_value = tmpdir
        cache_dir = Path(tmpdir).joinpath(NOTICES_CACHE_SUBDIR)
        cache_dir.mkdir(parents=True, exist_ok=True)

        yield cache_dir


@pytest.fixture(scope="function")
def notices_mock_http_session_get():
    with mock.patch("conda.gateways.connection.session.CondaSession.get") as session_get:
        yield session_get


@pytest.fixture(scope="function")
def conda_notices_args_n_parser():
    parser = conda_argparse.generate_parser()
    args = parser.parse_args(["notices"])

    return args, parser
