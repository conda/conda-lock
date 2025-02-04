from __future__ import annotations

from conda_lock._vendor.poetry.core.version.pep440.segments import LocalSegmentType
from conda_lock._vendor.poetry.core.version.pep440.segments import Release
from conda_lock._vendor.poetry.core.version.pep440.segments import ReleaseTag
from conda_lock._vendor.poetry.core.version.pep440.version import PEP440Version


__all__ = ("LocalSegmentType", "PEP440Version", "Release", "ReleaseTag")
