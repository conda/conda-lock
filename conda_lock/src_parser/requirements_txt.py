"""
Parses pip requirements from a requirements file
"""
from packaging._parser import parse_requirement
from pip._internal.network.session import PipSession
from pip._internal.req.req_file import parse_requirements

from conda_lock.models.lock_spec import VersionedDependency, VCSDependency, URLDependency, Dependency
from conda_lock.src_parser.pyproject_toml import unpack_git_url


def parse_requirements_txt(file_path, category=None) -> Dependency:
    session = PipSession()
    for req in filter(None, parse_requirements(file_path, session)):
        yield parse_one_requirement(req.requirement, category=category)


def parse_one_requirement(req_string: str, category=None) -> Dependency:
    parsed_req = parse_requirement(req_string)

    if parsed_req.url and parsed_req.url.startswith("git+"):
        url, rev = unpack_git_url(parsed_req.url)
        return VCSDependency(
            name=parsed_req.name,
            source=url,
            manager='pip',
            vcs="git",
            rev=rev,
        )
    elif parsed_req.url:  # type: ignore[attr-defined]
        assert parsed_req.specifier in {"", "*", None}
        url, frag = urldefrag(parsed_req.url)  # type: ignore[attr-defined]
        return URLDependency(
            name=parsed_req.name,
            manager='pip',
            category=category,
            extras=parsed_req.extras,
            url=url,
            hashes=[frag.replace("=", ":")],
        )
    else:
        return VersionedDependency(
            name=parsed_req.name,
            version=parsed_req.specifier or "*",
            manager='pip',
            category=category,
            extras=parsed_req.extras,
        )
