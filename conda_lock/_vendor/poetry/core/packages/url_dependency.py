from typing import TYPE_CHECKING
from typing import FrozenSet
from typing import List
from typing import Union

from conda_lock._vendor.poetry.core.utils._compat import urlparse

from .dependency import Dependency


if TYPE_CHECKING:
    from .constraints import BaseConstraint


class URLDependency(Dependency):
    def __init__(
        self,
        name,  # type: str
        url,  # type: str
        category="main",  # type: str
        optional=False,  # type: bool
        extras=None,  # type: Union[List[str], FrozenSet[str]]
    ):
        self._url = url

        parsed = urlparse.urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("{} does not seem like a valid url".format(url))

        super(URLDependency, self).__init__(
            name,
            "*",
            category=category,
            optional=optional,
            allows_prereleases=True,
            source_type="url",
            source_url=self._url,
            extras=extras,
        )

    @property
    def url(self):  # type: () -> str
        return self._url

    @property
    def base_pep_508_name(self):  # type: () -> str
        requirement = self.pretty_name

        if self.extras:
            requirement += "[{}]".format(",".join(self.extras))

        requirement += " @ {}".format(self._url)

        return requirement

    def is_url(self):  # type: () -> bool
        return True

    def with_constraint(self, constraint):  # type: ("BaseConstraint") -> URLDependency
        new = URLDependency(
            self.pretty_name,
            url=self._url,
            optional=self.is_optional(),
            category=self.category,
            extras=self._extras,
        )

        new._constraint = constraint
        new._pretty_constraint = str(constraint)

        new.is_root = self.is_root
        new.python_versions = self.python_versions
        new.marker = self.marker
        new.transitive_marker = self.transitive_marker

        for in_extra in self.in_extras:
            new.in_extras.append(in_extra)

        return new

    def __str__(self):  # type: () -> str
        return "{} ({} url)".format(self._pretty_name, self._pretty_constraint)

    def __hash__(self):  # type: () -> int
        return hash((self._name, self._url))
