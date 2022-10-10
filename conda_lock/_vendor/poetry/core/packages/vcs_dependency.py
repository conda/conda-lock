from typing import TYPE_CHECKING
from typing import FrozenSet
from typing import List
from typing import Optional
from typing import Union

from conda_lock._vendor.poetry.core.vcs import git

from .dependency import Dependency


if TYPE_CHECKING:
    from .constraints import BaseConstraint


class VCSDependency(Dependency):
    """
    Represents a VCS dependency
    """

    def __init__(
        self,
        name,  # type: str
        vcs,  # type: str
        source,  # type: str
        branch=None,  # type: Optional[str]
        tag=None,  # type: Optional[str]
        rev=None,  # type: Optional[str]
        resolved_rev=None,  # type: Optional[str]
        category="main",  # type: str
        optional=False,  # type: bool
        develop=False,  # type: bool
        extras=None,  # type: Union[List[str], FrozenSet[str]]
    ):
        self._vcs = vcs
        self._source = source

        if not any([branch, tag, rev]):
            # If nothing has been specified, we assume master
            branch = "master"

        self._branch = branch
        self._tag = tag
        self._rev = rev
        self._develop = develop

        super(VCSDependency, self).__init__(
            name,
            "*",
            category=category,
            optional=optional,
            allows_prereleases=True,
            source_type=self._vcs.lower(),
            source_url=self._source,
            source_reference=branch or tag or rev,
            source_resolved_reference=resolved_rev,
            extras=extras,
        )

    @property
    def vcs(self):  # type: () -> str
        return self._vcs

    @property
    def source(self):  # type: () -> str
        return self._source

    @property
    def branch(self):  # type: () -> Optional[str]
        return self._branch

    @property
    def tag(self):  # type: () -> Optional[str]
        return self._tag

    @property
    def rev(self):  # type: () -> Optional[str]
        return self._rev

    @property
    def develop(self):  # type: () -> bool
        return self._develop

    @property
    def reference(self):  # type: () -> str
        return self._branch or self._tag or self._rev

    @property
    def pretty_constraint(self):  # type: () -> str
        if self._branch:
            what = "branch"
            version = self._branch
        elif self._tag:
            what = "tag"
            version = self._tag
        else:
            what = "rev"
            version = self._rev

        return "{} {}".format(what, version)

    @property
    def base_pep_508_name(self):  # type: () -> str
        requirement = self.pretty_name
        parsed_url = git.ParsedUrl.parse(self._source)

        if self.extras:
            requirement += "[{}]".format(",".join(self.extras))

        if parsed_url.protocol is not None:
            requirement += " @ {}+{}@{}".format(self._vcs, self._source, self.reference)
        else:
            requirement += " @ {}+ssh://{}@{}".format(
                self._vcs, parsed_url.format(), self.reference
            )

        return requirement

    def is_vcs(self):  # type: () -> bool
        return True

    def accepts_prereleases(self):  # type: () -> bool
        return True

    def with_constraint(self, constraint):  # type: ("BaseConstraint") -> VCSDependency
        new = VCSDependency(
            self.pretty_name,
            self._vcs,
            self._source,
            branch=self._branch,
            tag=self._tag,
            rev=self._rev,
            resolved_rev=self._source_resolved_reference,
            optional=self.is_optional(),
            category=self.category,
            develop=self._develop,
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
        reference = self._vcs
        if self._branch:
            reference += " branch {}".format(self._branch)
        elif self._tag:
            reference += " tag {}".format(self._tag)
        elif self._rev:
            reference += " rev {}".format(self._rev)

        return "{} ({} {})".format(self._pretty_name, self._constraint, reference)

    def __hash__(self):  # type: () -> int
        return hash((self._name, self._vcs, self._branch, self._tag, self._rev))
