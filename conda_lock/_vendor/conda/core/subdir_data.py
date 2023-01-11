# -*- coding: utf-8 -*-
# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
from __future__ import absolute_import, division, print_function, unicode_literals

import bz2
from collections import defaultdict
from contextlib import closing
from errno import EACCES, ENODEV, EPERM, EROFS
from functools import partial
from genericpath import getmtime, isfile
import hashlib
from io import open as io_open
import json
from logging import DEBUG, getLogger
from mmap import ACCESS_READ, mmap
from os.path import dirname, isdir, join, splitext, exists
import re
from time import time
import warnings

try:
    from tlz.itertoolz import concat, groupby, take
except ImportError:
    from conda_lock._vendor.conda._vendor.toolz.itertoolz import concat, groupby, take

from .. import CondaError
from ..auxlib.ish import dals
from ..auxlib.logz import stringify
from .._vendor.boltons.setutils import IndexedSet
from ..base.constants import CONDA_HOMEPAGE_URL, CONDA_PACKAGE_EXTENSION_V1, REPODATA_FN
from ..base.context import context
from ..common.compat import ensure_binary, ensure_text_type, ensure_unicode
from ..common.io import ThreadLimitedThreadPoolExecutor, DummyExecutor, dashlist
from ..common.path import url_to_path
from ..common.url import join_url, maybe_unquote
from ..trust.signature_verification import signature_verification
from ..core.package_cache_data import PackageCacheData
from ..exceptions import (
    CondaDependencyError,
    CondaHTTPError,
    CondaUpgradeError,
    CondaSSLError,
    NotWritableError,
    UnavailableInvalidChannel,
    ProxyError,
)
from ..gateways.connection import (
    ConnectionError,
    HTTPError,
    InsecureRequestWarning,
    InvalidSchema,
    SSLError,
    RequestsProxyError,
)
from ..gateways.connection.session import CondaSession
from ..gateways.disk import mkdir_p, mkdir_p_sudo_safe
from ..gateways.disk.delete import rm_rf
from ..gateways.disk.update import touch
from ..models.channel import Channel, all_channel_urls
from ..models.match_spec import MatchSpec
from ..models.records import PackageRecord

try:
    import cPickle as pickle
except ImportError:  # pragma: no cover
    import pickle  # NOQA

log = getLogger(__name__)
stderrlog = getLogger('conda.stderrlog')

REPODATA_PICKLE_VERSION = 28
MAX_REPODATA_VERSION = 1
REPODATA_HEADER_RE = b'"(_etag|_mod|_cache_control)":[ ]?"(.*?[^\\\\])"[,}\\s]'  # NOQA


class SubdirDataType(type):

    def __call__(cls, channel, repodata_fn=REPODATA_FN):
        assert channel.subdir
        assert not channel.package_filename
        assert type(channel) is Channel
        now = time()
        repodata_fn = repodata_fn or REPODATA_FN
        cache_key = channel.url(with_credentials=True), repodata_fn
        if cache_key in SubdirData._cache_:
            cache_entry = SubdirData._cache_[cache_key]
            if cache_key[0].startswith('file://'):
                file_path = url_to_path(channel.url() + '/' + repodata_fn)
                if exists(file_path):
                    if cache_entry._mtime > getmtime(file_path):
                        return cache_entry
            else:
                return cache_entry
        subdir_data_instance = super(SubdirDataType, cls).__call__(channel, repodata_fn)
        subdir_data_instance._mtime = now
        SubdirData._cache_[cache_key] = subdir_data_instance
        return subdir_data_instance


class SubdirData(metaclass=SubdirDataType):
    _cache_ = {}

    @classmethod
    def clear_cached_local_channel_data(cls):
        # This should only ever be needed during unit tests, when
        # CONDA_USE_ONLY_TAR_BZ2 may change during process lifetime.
        cls._cache_ = {k: v for k, v in cls._cache_.items() if not k[0].startswith('file://')}

    @staticmethod
    def query_all(package_ref_or_match_spec, channels=None, subdirs=None,
                  repodata_fn=REPODATA_FN):
        from .index import check_allowlist  # TODO: fix in-line import
        # ensure that this is not called by threaded code
        create_cache_dir()
        if channels is None:
            channels = context.channels
        if subdirs is None:
            subdirs = context.subdirs
        channel_urls = all_channel_urls(channels, subdirs=subdirs)
        if context.offline:
            grouped_urls = groupby(lambda url: url.startswith('file://'), channel_urls)
            ignored_urls = grouped_urls.get(False, ())
            if ignored_urls:
                log.info("Ignoring the following channel urls because mode is offline.%s",
                         dashlist(ignored_urls))
            channel_urls = IndexedSet(grouped_urls.get(True, ()))
        check_allowlist(channel_urls)
        subdir_query = lambda url: tuple(SubdirData(Channel(url), repodata_fn=repodata_fn).query(
            package_ref_or_match_spec))

        # TODO test timing with ProcessPoolExecutor
        Executor = (DummyExecutor if context.debug or context.repodata_threads == 1
                    else partial(ThreadLimitedThreadPoolExecutor,
                                 max_workers=context.repodata_threads))
        with Executor() as executor:
            result = tuple(concat(executor.map(subdir_query, channel_urls)))
        return result

    def query(self, package_ref_or_match_spec):
        if not self._loaded:
            self.load()
        param = package_ref_or_match_spec
        if isinstance(param, str):
            param = MatchSpec(param)
        if isinstance(param, MatchSpec):
            if param.get_exact_value('name'):
                package_name = param.get_exact_value('name')
                for prec in self._names_index[package_name]:
                    if param.match(prec):
                        yield prec
            elif param.get_exact_value('track_features'):
                track_features = param.get_exact_value('track') or ()
                candidates = concat(self._track_features_index[feature_name]
                                    for feature_name in track_features)
                for prec in candidates:
                    if param.match(prec):
                        yield prec
            else:
                for prec in self._package_records:
                    if param.match(prec):
                        yield prec
        else:
            assert isinstance(param, PackageRecord)
            for prec in self._names_index[param.name]:
                if prec == param:
                    yield prec

    def __init__(self, channel, repodata_fn=REPODATA_FN):
        assert channel.subdir
        if channel.package_filename:
            parts = channel.dump()
            del parts['package_filename']
            channel = Channel(**parts)
        self.channel = channel
        self.url_w_subdir = self.channel.url(with_credentials=False)
        self.url_w_credentials = self.channel.url(with_credentials=True)
        # whether or not to try using the new, trimmed-down repodata
        self.repodata_fn = repodata_fn
        self._loaded = False
        self._key_mgr = None

    def reload(self):
        self._loaded = False
        self.load()
        return self

    @property
    def cache_path_base(self):
        return join(
            create_cache_dir(),
            splitext(cache_fn_url(self.url_w_credentials, self.repodata_fn))[0])

    @property
    def url_w_repodata_fn(self):
        return self.url_w_subdir + '/' + self.repodata_fn

    @property
    def cache_path_json(self):
        return self.cache_path_base + ('1' if context.use_only_tar_bz2 else '') + '.json'

    @property
    def cache_path_pickle(self):
        return self.cache_path_base + ('1' if context.use_only_tar_bz2 else '') + '.q'

    def load(self):
        _internal_state = self._load()
        if _internal_state.get("repodata_version", 0) > MAX_REPODATA_VERSION:
            raise CondaUpgradeError(dals("""
                The current version of conda is too old to read repodata from

                    %s

                (This version only supports repodata_version 1.)
                Please update conda to use this channel.
                """) % self.url_w_repodata_fn)

        self._internal_state = _internal_state
        self._package_records = _internal_state['_package_records']
        self._names_index = _internal_state['_names_index']
        self._track_features_index = _internal_state['_track_features_index']
        self._loaded = True
        return self

    def iter_records(self):
        if not self._loaded:
            self.load()
        return iter(self._package_records)

    def _load(self):
        try:
            mtime = getmtime(self.cache_path_json)
        except (IOError, OSError):
            log.debug("No local cache found for %s at %s", self.url_w_repodata_fn,
                      self.cache_path_json)
            if context.use_index_cache or (context.offline
                                           and not self.url_w_subdir.startswith('file://')):
                log.debug("Using cached data for %s at %s forced. Returning empty repodata.",
                          self.url_w_repodata_fn, self.cache_path_json)
                return {
                    '_package_records': (),
                    '_names_index': defaultdict(list),
                    '_track_features_index': defaultdict(list),
                }
            else:
                mod_etag_headers = {}
        else:
            mod_etag_headers = read_mod_and_etag(self.cache_path_json)

            if context.use_index_cache:
                log.debug("Using cached repodata for %s at %s because use_cache=True",
                          self.url_w_repodata_fn, self.cache_path_json)

                _internal_state = self._read_local_repdata(mod_etag_headers.get('_etag'),
                                                           mod_etag_headers.get('_mod'))
                return _internal_state

            if context.local_repodata_ttl > 1:
                max_age = context.local_repodata_ttl
            elif context.local_repodata_ttl == 1:
                max_age = get_cache_control_max_age(mod_etag_headers.get('_cache_control', ''))
            else:
                max_age = 0

            timeout = mtime + max_age - time()
            if (timeout > 0 or context.offline) and not self.url_w_subdir.startswith('file://'):
                log.debug("Using cached repodata for %s at %s. Timeout in %d sec",
                          self.url_w_repodata_fn, self.cache_path_json, timeout)
                _internal_state = self._read_local_repdata(mod_etag_headers.get('_etag'),
                                                           mod_etag_headers.get('_mod'))
                return _internal_state

            log.debug("Local cache timed out for %s at %s",
                      self.url_w_repodata_fn, self.cache_path_json)

        try:
            raw_repodata_str = fetch_repodata_remote_request(
                self.url_w_credentials,
                mod_etag_headers.get('_etag'),
                mod_etag_headers.get('_mod'),
                repodata_fn=self.repodata_fn)
            # empty file
            if not raw_repodata_str and self.repodata_fn != REPODATA_FN:
                raise UnavailableInvalidChannel(self.url_w_repodata_fn, 404)
        except UnavailableInvalidChannel:
            if self.repodata_fn != REPODATA_FN:
                self.repodata_fn = REPODATA_FN
                return self._load()
            else:
                raise
        except Response304ContentUnchanged:
            log.debug("304 NOT MODIFIED for '%s'. Updating mtime and loading from disk",
                      self.url_w_repodata_fn)
            touch(self.cache_path_json)
            _internal_state = self._read_local_repdata(mod_etag_headers.get('_etag'),
                                                       mod_etag_headers.get('_mod'))
            return _internal_state
        else:
            if not isdir(dirname(self.cache_path_json)):
                mkdir_p(dirname(self.cache_path_json))
            try:
                with io_open(self.cache_path_json, 'w') as fh:
                    fh.write(raw_repodata_str or '{}')
            except (IOError, OSError) as e:
                if e.errno in (EACCES, EPERM, EROFS):
                    raise NotWritableError(self.cache_path_json, e.errno, caused_by=e)
                else:
                    raise
            _internal_state = self._process_raw_repodata_str(raw_repodata_str)
            self._internal_state = _internal_state
            self._pickle_me()
            return _internal_state

    def _pickle_me(self):
        try:
            log.debug("Saving pickled state for %s at %s", self.url_w_repodata_fn,
                      self.cache_path_pickle)
            with open(self.cache_path_pickle, 'wb') as fh:
                pickle.dump(self._internal_state, fh, -1)  # -1 means HIGHEST_PROTOCOL
        except Exception:
            log.debug("Failed to dump pickled repodata.", exc_info=True)

    def _read_local_repdata(self, etag, mod_stamp):
        # first try reading pickled data
        _pickled_state = self._read_pickled(etag, mod_stamp)
        if _pickled_state:
            return _pickled_state

        # pickled data is bad or doesn't exist; load cached json
        log.debug("Loading raw json for %s at %s", self.url_w_repodata_fn, self.cache_path_json)
        with open(self.cache_path_json) as fh:
            try:
                raw_repodata_str = fh.read()
            except ValueError as e:
                # ValueError: Expecting object: line 11750 column 6 (char 303397)
                log.debug("Error for cache path: '%s'\n%r", self.cache_path_json, e)
                message = dals("""
                An error occurred when loading cached repodata.  Executing
                `conda clean --index-cache` will remove cached repodata files
                so they can be downloaded again.
                """)
                raise CondaError(message)
            else:
                _internal_state = self._process_raw_repodata_str(raw_repodata_str)
                self._internal_state = _internal_state
                self._pickle_me()
                return _internal_state

    def _read_pickled(self, etag, mod_stamp):

        if not isfile(self.cache_path_pickle) or not isfile(self.cache_path_json):
            # Don't trust pickled data if there is no accompanying json data
            return None

        try:
            if isfile(self.cache_path_pickle):
                log.debug("found pickle file %s", self.cache_path_pickle)
            with open(self.cache_path_pickle, 'rb') as fh:
                _pickled_state = pickle.load(fh)
        except Exception:
            log.debug("Failed to load pickled repodata.", exc_info=True)
            rm_rf(self.cache_path_pickle)
            return None

        def _check_pickled_valid():
            yield _pickled_state.get('_url') == self.url_w_credentials
            yield _pickled_state.get('_schannel') == self.channel.canonical_name
            yield _pickled_state.get('_add_pip') == context.add_pip_as_python_dependency
            yield _pickled_state.get('_mod') == mod_stamp
            yield _pickled_state.get('_etag') == etag
            yield _pickled_state.get('_pickle_version') == REPODATA_PICKLE_VERSION
            yield _pickled_state.get('fn') == self.repodata_fn

        if not all(_check_pickled_valid()):
            log.debug("Pickle load validation failed for %s at %s.",
                      self.url_w_repodata_fn, self.cache_path_json)
            return None

        return _pickled_state

    def _process_raw_repodata_str(self, raw_repodata_str):
        json_obj = json.loads(raw_repodata_str or '{}')
        return self._process_raw_repodata(json_obj)

    def _process_raw_repodata(self, repodata):
        subdir = repodata.get('info', {}).get('subdir') or self.channel.subdir
        assert subdir == self.channel.subdir
        add_pip = context.add_pip_as_python_dependency
        schannel = self.channel.canonical_name

        self._package_records = _package_records = []
        self._names_index = _names_index = defaultdict(list)
        self._track_features_index = _track_features_index = defaultdict(list)

        signatures = repodata.get("signatures", {})

        _internal_state = {
            'channel': self.channel,
            'url_w_subdir': self.url_w_subdir,
            'url_w_credentials': self.url_w_credentials,
            'cache_path_base': self.cache_path_base,
            'fn': self.repodata_fn,

            '_package_records': _package_records,
            '_names_index': _names_index,
            '_track_features_index': _track_features_index,

            '_etag': repodata.get('_etag'),
            '_mod': repodata.get('_mod'),
            '_cache_control': repodata.get('_cache_control'),
            '_url': repodata.get('_url'),
            '_add_pip': add_pip,
            '_pickle_version': REPODATA_PICKLE_VERSION,
            '_schannel': schannel,
            'repodata_version': repodata.get('repodata_version', 0),
        }
        if _internal_state["repodata_version"] > MAX_REPODATA_VERSION:
            raise CondaUpgradeError(dals("""
                The current version of conda is too old to read repodata from

                    %s

                (This version only supports repodata_version 1.)
                Please update conda to use this channel.
                """) % self.url_w_subdir)

        meta_in_common = {  # just need to make this once, then apply with .update()
            'arch': repodata.get('info', {}).get('arch'),
            'channel': self.channel,
            'platform': repodata.get('info', {}).get('platform'),
            'schannel': schannel,
            'subdir': subdir,
        }

        channel_url = self.url_w_credentials
        legacy_packages = repodata.get("packages", {})
        conda_packages = {} if context.use_only_tar_bz2 else repodata.get("packages.conda", {})

        _tar_bz2 = CONDA_PACKAGE_EXTENSION_V1
        use_these_legacy_keys = set(legacy_packages.keys()) - set(
            k[:-6] + _tar_bz2 for k in conda_packages.keys()
        )

        for group, copy_legacy_md5 in (
                (conda_packages.items(), True),
                (((k, legacy_packages[k]) for k in use_these_legacy_keys), False)):
            for fn, info in group:

                # Verify metadata signature before anything else so run-time
                # updates to the info dictionary performed below do not
                # invalidate the signatures provided in metadata.json.
                signature_verification(info, fn, signatures)

                info['fn'] = fn
                info['url'] = join_url(channel_url, fn)
                if copy_legacy_md5:
                    counterpart = fn.replace('.conda', '.tar.bz2')
                    if counterpart in legacy_packages:
                        info['legacy_bz2_md5'] = legacy_packages[counterpart].get('md5')
                        info['legacy_bz2_size'] = legacy_packages[counterpart].get('size')
                if (add_pip and info['name'] == 'python' and
                        info['version'].startswith(('2.', '3.'))):
                    info['depends'].append('pip')
                info.update(meta_in_common)
                if info.get('record_version', 0) > 1:
                    log.debug("Ignoring record_version %d from %s",
                              info["record_version"], info['url'])
                    continue

                package_record = PackageRecord(**info)

                _package_records.append(package_record)
                _names_index[package_record.name].append(package_record)
                for ftr_name in package_record.track_features:
                    _track_features_index[ftr_name].append(package_record)

        self._internal_state = _internal_state
        return _internal_state


def read_mod_and_etag(path):
    with open(path, 'rb') as f:
        try:
            with closing(mmap(f.fileno(), 0, access=ACCESS_READ)) as m:
                match_objects = take(3, re.finditer(REPODATA_HEADER_RE, m))
                result = dict(map(ensure_unicode, mo.groups()) for mo in match_objects)
                return result
        except (BufferError, ValueError):  # pragma: no cover
            # BufferError: cannot close exported pointers exist
            #   https://github.com/conda/conda/issues/4592
            # ValueError: cannot mmap an empty file
            return {}
        except OSError as e:  # pragma: no cover
            # OSError: [Errno 19] No such device
            if e.errno == ENODEV:
                return {}
            raise


def get_cache_control_max_age(cache_control_value):
    max_age = re.search(r"max-age=(\d+)", cache_control_value)
    return int(max_age.groups()[0]) if max_age else 0


class Response304ContentUnchanged(Exception):
    pass


def fetch_repodata_remote_request(url, etag, mod_stamp, repodata_fn=REPODATA_FN):
    if not context.ssl_verify:
        warnings.simplefilter('ignore', InsecureRequestWarning)

    session = CondaSession()

    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if mod_stamp:
        headers["If-Modified-Since"] = mod_stamp

    headers['Accept-Encoding'] = 'gzip, deflate, compress, identity'
    headers['Accept'] = 'application/json'
    filename = repodata_fn

    try:
        timeout = context.remote_connect_timeout_secs, context.remote_read_timeout_secs
        resp = session.get(join_url(url, filename), headers=headers, proxies=session.proxies,
                           timeout=timeout)
        if log.isEnabledFor(DEBUG):
            log.debug(stringify(resp, content_max_len=256))
        resp.raise_for_status()

    except RequestsProxyError:
        raise ProxyError()   # see #3962

    except InvalidSchema as e:
        if 'SOCKS' in str(e):
            message = dals("""
            Requests has identified that your current working environment is configured
            to use a SOCKS proxy, but pysocks is not installed.  To proceed, remove your
            proxy configuration, run `conda install pysocks`, and then you can re-enable
            your proxy configuration.
            """)
            raise CondaDependencyError(message)
        else:
            raise

    except SSLError as e:
        # SSLError: either an invalid certificate or OpenSSL is unavailable
        try:
            import ssl  # noqa: F401
        except ImportError:
            raise CondaSSLError(
                dals(
                    f"""
                    OpenSSL appears to be unavailable on this machine. OpenSSL is required to
                    download and install packages.

                    Exception: {e}
                    """
                )
            )
        else:
            raise CondaSSLError(
                dals(
                    f"""
                    Encountered an SSL error. Most likely a certificate verification issue.

                    Exception: {e}
                    """
                )
            )

    except (ConnectionError, HTTPError) as e:
        status_code = getattr(e.response, 'status_code', None)
        if status_code in (403, 404):
            if not url.endswith('/noarch'):
                log.info("Unable to retrieve repodata (response: %d) for %s", status_code,
                         url + '/' + repodata_fn)
                return None
            else:
                if context.allow_non_channel_urls:
                    stderrlog.warning("Unable to retrieve repodata (response: %d) for %s",
                                      status_code, url + '/' + repodata_fn)
                    return None
                else:
                    raise UnavailableInvalidChannel(
                        Channel(dirname(url)),
                        status_code,
                        response=e.response,
                    )

        elif status_code == 401:
            channel = Channel(url)
            if channel.token:
                help_message = dals("""
                The token '%s' given for the URL is invalid.

                If this token was pulled from anaconda-client, you will need to use
                anaconda-client to reauthenticate.

                If you supplied this token to conda directly, you will need to adjust your
                conda configuration to proceed.

                Use `conda config --show` to view your configuration's current state.
                Further configuration help can be found at <%s>.
               """) % (channel.token, join_url(CONDA_HOMEPAGE_URL, 'docs/config.html'))

            elif context.channel_alias.location in url:
                # Note, this will not trigger if the binstar configured url does
                # not match the conda configured one.
                help_message = dals("""
                The remote server has indicated you are using invalid credentials for this channel.

                If the remote site is anaconda.org or follows the Anaconda Server API, you
                will need to
                  (a) remove the invalid token from your system with `anaconda logout`, optionally
                      followed by collecting a new token with `anaconda login`, or
                  (b) provide conda with a valid token directly.

                Further configuration help can be found at <%s>.
               """) % join_url(CONDA_HOMEPAGE_URL, 'docs/config.html')

            else:
                help_message = dals("""
                The credentials you have provided for this URL are invalid.

                You will need to modify your conda configuration to proceed.
                Use `conda config --show` to view your configuration's current state.
                Further configuration help can be found at <%s>.
                """) % join_url(CONDA_HOMEPAGE_URL, 'docs/config.html')

        elif status_code is not None and 500 <= status_code < 600:
            help_message = dals("""
            A remote server error occurred when trying to retrieve this URL.

            A 500-type error (e.g. 500, 501, 502, 503, etc.) indicates the server failed to
            fulfill a valid request.  The problem may be spurious, and will resolve itself if you
            try your request again.  If the problem persists, consider notifying the maintainer
            of the remote server.
            """)

        else:
            if url.startswith("https://repo.anaconda.com/"):
                help_message = dals("""
                An HTTP error occurred when trying to retrieve this URL.
                HTTP errors are often intermittent, and a simple retry will get you on your way.

                If your current network has https://www.anaconda.com blocked, please file
                a support request with your network engineering team.

                %s
                """) % maybe_unquote(repr(url))
            else:
                help_message = dals("""
                An HTTP error occurred when trying to retrieve this URL.
                HTTP errors are often intermittent, and a simple retry will get you on your way.
                %s
                """) % maybe_unquote(repr(url))

        raise CondaHTTPError(help_message,
                             join_url(url, filename),
                             status_code,
                             getattr(e.response, 'reason', None),
                             getattr(e.response, 'elapsed', None),
                             e.response,
                             caused_by=e)

    if resp.status_code == 304:
        raise Response304ContentUnchanged()

    def maybe_decompress(filename, resp_content):
        return ensure_text_type(bz2.decompress(resp_content)
                                if filename.endswith('.bz2')
                                else resp_content).strip()

    json_str = maybe_decompress(filename, resp.content)

    saved_fields = {'_url': url}
    add_http_value_to_dict(resp, 'Etag', saved_fields, '_etag')
    add_http_value_to_dict(resp, 'Last-Modified', saved_fields, '_mod')
    add_http_value_to_dict(resp, 'Cache-Control', saved_fields, '_cache_control')

    # add extra values to the raw repodata json
    if json_str and json_str != "{}":
        raw_repodata_str = u"%s, %s" % (
            json.dumps(saved_fields)[:-1],  # remove trailing '}'
            json_str[1:]  # remove first '{'
        )
    else:
        raw_repodata_str = ensure_text_type(json.dumps(saved_fields))
    return raw_repodata_str


def make_feature_record(feature_name):
    # necessary for the SAT solver to do the right thing with features
    pkg_name = "%s@" % feature_name
    return PackageRecord(
        name=pkg_name,
        version='0',
        build='0',
        channel='@',
        subdir=context.subdir,
        md5="12345678901234567890123456789012",
        track_features=(feature_name,),
        build_number=0,
        fn=pkg_name,
    )


def cache_fn_url(url, repodata_fn=REPODATA_FN):
    # url must be right-padded with '/' to not invalidate any existing caches
    if not url.endswith('/'):
        url += '/'
    # add the repodata_fn in for uniqueness, but keep it off for standard stuff.
    #    It would be more sane to add it for everything, but old programs (Navigator)
    #    are looking for the cache under keys without this.
    if repodata_fn != REPODATA_FN:
        url += repodata_fn

    # TODO: remove try-except when conda only supports Python 3.9+, as
    # `usedforsecurity=False` was added in 3.9.
    try:
        md5 = hashlib.md5(ensure_binary(url))
    except ValueError:
        md5 = hashlib.md5(ensure_binary(url), usedforsecurity=False)
    return '%s.json' % (md5.hexdigest()[:8],)


def add_http_value_to_dict(resp, http_key, d, dict_key):
    value = resp.headers.get(http_key)
    if value:
        d[dict_key] = value


def create_cache_dir():
    cache_dir = join(PackageCacheData.first_writable().pkgs_dir, 'cache')
    mkdir_p_sudo_safe(cache_dir)
    return cache_dir
