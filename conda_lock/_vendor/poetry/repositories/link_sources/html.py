from __future__ import annotations

import urllib.parse

from collections import defaultdict
from functools import cached_property
from html import unescape
from typing import TYPE_CHECKING

from conda_lock._vendor.poetry.core.packages.utils.link import Link

from conda_lock._vendor.poetry.repositories.link_sources.base import LinkSource
from conda_lock._vendor.poetry.repositories.parsers.html_page_parser import HTMLPageParser


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.repositories.link_sources.base import LinkCache


class HTMLPage(LinkSource):
    def __init__(self, url: str, content: str) -> None:
        super().__init__(url=url)

        parser = HTMLPageParser()
        parser.feed(content)
        self._parsed = parser.anchors
        self._base_url: str | None = parser.base_url

    @cached_property
    def _link_cache(self) -> LinkCache:
        links: LinkCache = defaultdict(lambda: defaultdict(list))
        for anchor in self._parsed:
            if href := anchor.get("href"):
                url = self.clean_link(
                    urllib.parse.urljoin(self._base_url or self._url, href)
                )
                pyrequire = anchor.get("data-requires-python")
                pyrequire = unescape(pyrequire) if pyrequire else None
                yanked_value = anchor.get("data-yanked")
                yanked: str | bool
                if yanked_value:
                    yanked = unescape(yanked_value)
                else:
                    yanked = "data-yanked" in anchor

                # see https://peps.python.org/pep-0714/#clients
                # and https://peps.python.org/pep-0658/#specification
                metadata: str | bool
                for metadata_key in ("data-core-metadata", "data-dist-info-metadata"):
                    metadata_value = anchor.get(metadata_key)
                    if metadata_value:
                        metadata = unescape(metadata_value)
                    else:
                        metadata = metadata_key in anchor
                    if metadata:
                        break
                link = Link(
                    url, requires_python=pyrequire, yanked=yanked, metadata=metadata
                )

                if link.ext not in self.SUPPORTED_FORMATS:
                    continue

                pkg = self.link_package_data(link)
                if pkg:
                    links[pkg.name][pkg.version].append(link)

        return links


class SimpleRepositoryPage(HTMLPage):
    def __init__(self, url: str, content: str) -> None:
        if not url.endswith("/"):
            url += "/"
        super().__init__(url=url, content=content)
