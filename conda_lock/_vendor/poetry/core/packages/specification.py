from typing import FrozenSet
from typing import List
from typing import Optional

from conda_lock._vendor.poetry.core.utils.helpers import canonicalize_name


class PackageSpecification(object):
    def __init__(
        self,
        name,  # type: str
        source_type=None,  # type: Optional[str]
        source_url=None,  # type: Optional[str]
        source_reference=None,  # type: Optional[str]
        source_resolved_reference=None,  # type: Optional[str]
        features=None,  # type: Optional[List[str]]
    ):
        self._pretty_name = name
        self._name = canonicalize_name(name)
        self._source_type = source_type
        self._source_url = source_url
        self._source_reference = source_reference
        self._source_resolved_reference = source_resolved_reference

        if not features:
            features = []

        self._features = frozenset(features)

    @property
    def name(self):  # type: () -> str
        return self._name

    @property
    def pretty_name(self):  # type: () -> str
        return self._pretty_name

    @property
    def complete_name(self):  # type: () -> str
        name = self._name

        if self._features:
            name = "{}[{}]".format(name, ",".join(sorted(self._features)))

        return name

    @property
    def source_type(self):  # type: () -> Optional[str]
        return self._source_type

    @property
    def source_url(self):  # type: () -> Optional[str]
        return self._source_url

    @property
    def source_reference(self):  # type: () -> Optional[str]
        return self._source_reference

    @property
    def source_resolved_reference(self):  # type: () -> Optional[str]
        return self._source_resolved_reference

    @property
    def features(self):  # type: () -> FrozenSet[str]
        return self._features

    def is_same_package_as(self, other):  # type: ("PackageSpecification") -> bool
        if other.complete_name != self.complete_name:
            return False

        if self._source_type:
            if self._source_type != other.source_type:
                return False

            if self._source_url or other.source_url:
                if self._source_url != other.source_url:
                    return False

            if self._source_reference or other.source_reference:
                # special handling for packages with references
                if not self._source_reference or not other.source_reference:
                    # case: one reference is defined and is non-empty, but other is not
                    return False

                if not (
                    self._source_reference == other.source_reference
                    or self._source_reference.startswith(other.source_reference)
                    or other.source_reference.startswith(self._source_reference)
                ):
                    # case: both references defined, but one is not equal to or a short
                    # representation of the other
                    return False

                if (
                    self._source_resolved_reference
                    and other.source_resolved_reference
                    and self._source_resolved_reference
                    != other.source_resolved_reference
                ):
                    return False

        return True

    def __hash__(self):  # type: () -> int
        if not self._source_type:
            return hash(self._name)

        return (
            hash(self._name)
            ^ hash(self._source_type)
            ^ hash(self._source_url)
            ^ hash(self._source_reference)
            ^ hash(self._source_resolved_reference)
            ^ hash(self._features)
        )

    def __str__(self):  # type: () -> str
        raise NotImplementedError()
