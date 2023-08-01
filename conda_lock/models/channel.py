import logging
from posixpath import expandvars
from urllib.parse import unquote, urlparse, urlunparse

from conda_lock.models.package_source import PackageSource, token_pattern


logger = logging.getLogger(__name__)


class Channel(PackageSource):
    def conda_token_replaced_url(self) -> str:
        """This is basically a crazy thing that conda does for the token replacement in the output"""
        # TODO: pass in env vars maybe?
        expanded_url = expandvars(self.url)
        if token_pattern.match(expanded_url):
            replaced = token_pattern.sub(r"\1\3", expanded_url, 1)
            p = urlparse(replaced)
            replaced = urlunparse(p._replace(path="/t/<TOKEN>" + p.path))
            return replaced
        return expanded_url
