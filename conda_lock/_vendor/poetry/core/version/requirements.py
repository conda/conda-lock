# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os

from lark import Lark
from lark import UnexpectedCharacters
from lark import UnexpectedToken

from conda_lock._vendor.poetry.core.semver import parse_constraint
from conda_lock._vendor.poetry.core.semver.exceptions import ParseConstraintError

from .markers import _compact_markers


try:
    import urllib.parse as urlparse
except ImportError:
    import urlparse


class InvalidRequirement(ValueError):
    """
    An invalid requirement was found, users should refer to PEP 508.
    """


_parser = Lark.open(
    os.path.join(os.path.dirname(__file__), "grammars", "pep508.lark"), parser="lalr"
)


class Requirement(object):
    """
    Parse a requirement.

    Parse a given requirement string into its parts, such as name, specifier,
    URL, and extras. Raises InvalidRequirement on a badly-formed requirement
    string.
    """

    def __init__(self, requirement_string):  # type: (str) -> None
        try:
            parsed = _parser.parse(requirement_string)
        except (UnexpectedCharacters, UnexpectedToken) as e:
            raise InvalidRequirement(
                "The requirement is invalid: Unexpected character at column {}\n\n{}".format(
                    e.column, e.get_context(requirement_string)
                )
            )

        self.name = next(parsed.scan_values(lambda t: t.type == "NAME")).value
        url = next(parsed.scan_values(lambda t: t.type == "URI"), None)

        if url:
            url = url.value
            parsed_url = urlparse.urlparse(url)
            if parsed_url.scheme == "file":
                if urlparse.urlunparse(parsed_url) != url:
                    raise InvalidRequirement(
                        'The requirement is invalid: invalid URL "{0}"'.format(url)
                    )
            elif (
                not (parsed_url.scheme and parsed_url.netloc)
                or (not parsed_url.scheme and not parsed_url.netloc)
            ) and not parsed_url.path:
                raise InvalidRequirement(
                    'The requirement is invalid: invalid URL "{0}"'.format(url)
                )
            self.url = url
        else:
            self.url = None

        self.extras = [e.value for e in parsed.scan_values(lambda t: t.type == "EXTRA")]
        constraint = next(parsed.find_data("version_specification"), None)
        if not constraint:
            constraint = "*"
        else:
            constraint = ",".join(constraint.children)

        try:
            self.constraint = parse_constraint(constraint)
        except ParseConstraintError:
            raise InvalidRequirement(
                'The requirement is invalid: invalid version constraint "{}"'.format(
                    constraint
                )
            )

        self.pretty_constraint = constraint

        marker = next(parsed.find_data("marker_spec"), None)
        if marker:
            marker = _compact_markers(
                marker.children[0].children, tree_prefix="markers__"
            )

        self.marker = marker

    def __str__(self):  # type: () -> str
        parts = [self.name]

        if self.extras:
            parts.append("[{0}]".format(",".join(sorted(self.extras))))

        if self.pretty_constraint:
            parts.append(self.pretty_constraint)

        if self.url:
            parts.append("@ {0}".format(self.url))

        if self.marker:
            parts.append("; {0}".format(self.marker))

        return "".join(parts)

    def __repr__(self):  # type: () -> str
        return "<Requirement({0!r})>".format(str(self))
