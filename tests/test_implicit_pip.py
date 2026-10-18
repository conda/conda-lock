"""A pip subsection must not displace explicit conda pip constraints (#919)."""

from itertools import product
from pathlib import Path
from unittest.mock import patch

import pytest

from conda_lock import conda_solver
from conda_lock._export_lock_spec_compute_platform_indep import (
    unify_platform_independent_deps,
)
from conda_lock.conda_lock import render_lockfile_for_platform
from conda_lock.conda_solver import solve_conda
from conda_lock.content_hash import compute_content_hashes
from conda_lock.lockfile.v2prelim.models import Lockfile, LockMeta
from conda_lock.models.lock_spec import (
    Dependency,
    LockSpecification,
    PathDependency,
    URLDependency,
    VCSDependency,
    VersionedDependency,
)
from conda_lock.src_parser import make_lock_spec


@pytest.fixture
def mapping_url(tmp_path: Path) -> str:
    mapping = tmp_path / "mapping.json"
    mapping.write_text("{}")
    return str(mapping)


def _conda_pip(spec: LockSpecification, platform: str) -> list[VersionedDependency]:
    return [
        d
        for d in spec.dependencies[platform]
        if isinstance(d, VersionedDependency)
        and (d.manager, d.name) == ("conda", "pip")
    ]


def _fake_solve(*args: object, **kwargs: object) -> dict:
    def fetch(name: str, version: str, depends: list[str]) -> dict:
        return {
            "name": name,
            "version": version,
            "depends": depends,
            "url": f"https://conda.anaconda.org/conda-forge/linux-64/{name}-{version}-0.conda",
            "md5": "0",
        }

    return {
        "actions": {
            "FETCH": [
                fetch("python", "3.12.1", ["openssl"]),
                fetch("openssl", "3.0.0", []),
                fetch("pip", "25.0.1", ["python", "setuptools"]),
                fetch("setuptools", "70.0.0", ["python"]),
            ]
        }
    }


@pytest.mark.parametrize(
    "requirements, conda_pip_version, pip_managed",
    [
        ("  - pip=25.0.1\n  - pip: [requests]\n", "25.0.1.*", [("requests", "*")]),
        ("  - pip: [requests]\n  - pip=25.0.1\n", "25.0.1.*", [("requests", "*")]),
        ("  - pip: [requests]\n", "*", [("requests", "*")]),
        ("  - pip\n  - pip: [requests]\n", "*", [("requests", "*")]),
        ("  - pip: [requests]\n  - pip\n", "*", [("requests", "*")]),
        ("  - conda-forge::pip\n  - pip: [requests]\n", "", [("requests", "*")]),
        ("  - pip=25.0.1\n", "25.0.1.*", []),
        (
            "  - pip: [requests]\n  - pip: [six]\n",
            "*",
            [("requests", "*"), ("six", "*")],
        ),
        (
            "  - pip=25.0.1\n  - pip: [requests]\n  - pip: [six]\n",
            "25.0.1.*",
            [("requests", "*"), ("six", "*")],
        ),
        ("  - pip: ['pip==24.0']\n", "*", [("pip", "==24.0")]),
        ("  - pip: []\n", "*", []),
        ("  - pip: null\n", None, []),
        ("  - pip: ['requests; sys_platform == \"win32\"']\n", "*", []),
        ("  - pip: ['-e ./local']\n", "*", []),
    ],
)
def test_pip_subsection(
    mapping_url: str,
    tmp_path: Path,
    requirements: str,
    conda_pip_version: str | None,
    pip_managed: list[tuple[str, str]],
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text("dependencies:\n  - python=3.12.1\n" + requirements)
    spec = make_lock_spec(
        src_files=[source], platform_overrides=["linux-64"], mapping_url=mapping_url
    )
    assert [d.version for d in _conda_pip(spec, "linux-64")] == (
        [] if conda_pip_version is None else [conda_pip_version]
    )
    assert sorted(
        (d.name, d.version)
        for d in spec.dependencies["linux-64"]
        if isinstance(d, VersionedDependency) and d.manager == "pip"
    ) == sorted(pip_managed)


def test_issue_919_solver_specs(tmp_path: Path, mapping_url: str) -> None:
    source = tmp_path / "environment.yml"
    source.write_text(
        "name: test\nchannels:\n  - conda-forge\ndependencies:\n"
        "  - python=3.12.1\n  - pip=25.0.1\n  - pip:\n      - requests\n"
    )
    spec = make_lock_spec(
        src_files=[source], platform_overrides=["linux-64"], mapping_url=mapping_url
    )
    # Capture the real solve_conda conversion at the external solver boundary.
    with patch.object(conda_solver, "solve_specs_for_arch") as solve:
        solve.side_effect = RuntimeError("solver boundary reached")
        with pytest.raises(RuntimeError, match="solver boundary reached"):
            solve_conda(
                conda="unused",
                specs={
                    d.name: d
                    for d in spec.dependencies["linux-64"]
                    if d.manager == "conda"
                },
                locked={},
                update=[],
                platform="linux-64",
                channels=spec.channels,
                mapping_url=mapping_url,
            )
    assert sorted(solve.call_args.kwargs["specs"]) == [
        "pip 25.0.1.*",
        "python 3.12.1.*",
    ]


@pytest.mark.parametrize("layout", ["same_file", "explicit_first", "subsection_first"])
@pytest.mark.parametrize("categories", [None, {"main"}, {"main", "dev"}, {"dev"}])
def test_pip_constraint_across_sources_and_categories(
    mapping_url: str,
    tmp_path: Path,
    layout: str,
    categories: set[str] | None,
) -> None:
    explicit = tmp_path / "explicit.yml"
    explicit.write_text(
        "category: dev\ndependencies:\n  - python\n"
        "  - conda-forge::pip=25.0.1=pyh8b19718_0  # [linux]\n"
    )
    subsection = "  - pip: [requests]\n"
    if layout == "same_file":
        explicit.write_text(explicit.read_text() + subsection)
        sources = [explicit]
    else:
        other = tmp_path / "subsection.yml"
        other.write_text("dependencies:\n" + subsection)
        sources = [explicit, other] if layout == "explicit_first" else [other, explicit]
    platforms = ["linux-64", "osx-arm64"]
    spec = make_lock_spec(
        src_files=sources,
        platform_overrides=platforms,
        filtered_categories=categories,
        mapping_url=mapping_url,
    )
    pin = ("25.0.1", "pyh8b19718_0", "conda-forge")
    fallback = ("*", None, None)
    main_wanted = categories is None or "main" in categories
    dev_wanted = categories is None or "dev" in categories
    for platform in platforms:
        pip = _conda_pip(spec, platform)
        if platform == "linux-64" and dev_wanted:
            # The explicit constraint survives. Its effective category is main
            # whenever the subsection's requirement applies, so a main-only
            # installation still receives pip.
            assert [(d.version, d.build, d.conda_channel) for d in pip] == [pin]
            assert [d.category for d in pip] == ["main" if main_wanted else "dev"]
        elif main_wanted:
            assert [(d.version, d.build, d.conda_channel) for d in pip] == [fallback]
            assert [d.category for d in pip] == ["main"]
        else:
            assert pip == []


@pytest.mark.parametrize("dev_first", [True, False])
def test_dev_pin_keeps_pip_in_main_installation(
    mapping_url: str, tmp_path: Path, dev_first: bool
) -> None:
    dev = tmp_path / "dev.yml"
    dev.write_text(
        "category: dev\nchannels: [conda-forge]\ndependencies:\n"
        "  - python=3.12.1\n  - pip=25.0.1\n"
    )
    main = tmp_path / "main.yml"
    main.write_text("channels: [conda-forge]\ndependencies:\n  - pip: [requests]\n")
    sources = [dev, main] if dev_first else [main, dev]
    spec = make_lock_spec(
        src_files=sources, platform_overrides=["linux-64"], mapping_url=mapping_url
    )
    with patch.object(
        conda_solver, "solve_specs_for_arch", side_effect=_fake_solve
    ) as solve:
        planned = solve_conda(
            conda="unused",
            specs={
                d.name: d for d in spec.dependencies["linux-64"] if d.manager == "conda"
            },
            locked={},
            update=[],
            platform="linux-64",
            channels=spec.channels,
            mapping_url=mapping_url,
        )
    assert "pip 25.0.1.*" in solve.call_args.kwargs["specs"]
    assert planned["pip"].categories == {"main"}
    assert planned["python"].categories == {"main"}
    lockfile = Lockfile(
        package=list(planned.values()),
        metadata=LockMeta(
            content_hash={"linux-64": "unused"},
            channels=spec.channels,
            platforms=["linux-64"],
            sources=[str(s) for s in sources],
        ),
    )
    rendered = render_lockfile_for_platform(
        lockfile=lockfile,
        include_dev_dependencies=False,
        extras=None,
        kind="explicit",
        platform="linux-64",
    )
    assert any("/pip-25.0.1-" in line for line in rendered)
    assert any("/python-3.12.1-" in line for line in rendered)


@pytest.mark.parametrize(
    "spec, expected",
    # The subsection's fallback is emitted after the file's string specs, so it
    # is the later of two unconstrained requirements and wins as before.
    [("pip", "*"), ("pip *", "*"), ("python", "")],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_explicit_duplicates_still_use_last_source(
    mapping_url: str, tmp_path: Path, spec: str, expected: str, reverse: bool
) -> None:
    name = spec.split()[0]
    sources = [tmp_path / "pin.yml", tmp_path / "unconstrained.yml"]
    sources[0].write_text(f"dependencies:\n  - {name}=25.0.1\n")
    sources[1].write_text(f"dependencies:\n  - {spec}\n  - pip: [requests]\n")
    if reverse:
        sources.reverse()
    lock_spec = make_lock_spec(
        src_files=sources, platform_overrides=["linux-64"], mapping_url=mapping_url
    )
    dep = next(
        d
        for d in lock_spec.dependencies["linux-64"]
        if d.name == name and d.manager == "conda"
    )
    assert isinstance(dep, VersionedDependency)
    assert dep.version == ("25.0.1.*" if reverse else expected)


# Content hashes computed on the base commit for the inputs below. Unconstrained
# explicit pip and the subsection's fallback keep ordinary last-wins semantics,
# including the winner's category, so nothing changes for these layouts.
_LEGACY_CASES = [
    # (unconstrained explicit pip, its category, subsection first, filter,
    #  surviving conda pip as (version, category) or None, base content hash)
    (
        "pip",
        "dev",
        True,
        None,
        ("", "dev"),
        "625c17a93d5975490ae982b6491edadc203114eb084459d1cf8188251e2caa74",
    ),
    (
        "pip *",
        "dev",
        True,
        None,
        ("*", "dev"),
        "a46be966d564bc349ea0519e35887c3045f6e427e75aef7341d41c3375bd72af",
    ),
    (
        "pip",
        "dev",
        True,
        {"main"},
        None,
        "7738af3245cca7b967246a94955891cc0618931843dc73c0e26aed13d789d58e",
    ),
    (
        "pip *",
        "dev",
        True,
        {"main"},
        None,
        "7738af3245cca7b967246a94955891cc0618931843dc73c0e26aed13d789d58e",
    ),
    (
        "pip",
        "dev",
        False,
        {"dev"},
        None,
        "9617407c233ba9a19fcddf09415061c5095993c1749dd20d1e3e20ac1340aa11",
    ),
    (
        "pip *",
        "dev",
        False,
        {"dev"},
        None,
        "9617407c233ba9a19fcddf09415061c5095993c1749dd20d1e3e20ac1340aa11",
    ),
    (
        "pip",
        "main",
        True,
        None,
        ("", "main"),
        "49b56486c96b79dd46bbda2867081ca62b86e1e2a957e38b6aa014195f22cf32",
    ),
    (
        "pip *",
        "main",
        True,
        None,
        ("*", "main"),
        "649cc3219c8549016232df0cdc61d1712cf2d38d3eec5ca7cd86c89dc176e858",
    ),
]


@pytest.mark.parametrize(
    "explicit_spec, category, subsection_first, categories, conda_pip, base_hash",
    _LEGACY_CASES,
    ids=[
        f"{case[0]}-{case[1]}-{'subsection_first' if case[2] else 'explicit_first'}"
        f"-{'unfiltered' if case[3] is None else '+'.join(sorted(case[3]))}"
        for case in _LEGACY_CASES
    ],
)
def test_unconstrained_pip_keeps_legacy_last_wins_and_hash(
    mapping_url: str,
    tmp_path: Path,
    explicit_spec: str,
    category: str,
    subsection_first: bool,
    categories: set[str] | None,
    conda_pip: tuple[str, str] | None,
    base_hash: str,
) -> None:
    subsection = tmp_path / "subsection.yml"
    subsection.write_text("dependencies:\n  - pip: [requests]\n")
    explicit = tmp_path / "explicit.yml"
    explicit.write_text(f"category: {category}\ndependencies:\n  - {explicit_spec}\n")
    sources = [subsection, explicit] if subsection_first else [explicit, subsection]
    spec = make_lock_spec(
        src_files=sources,
        platform_overrides=["linux-64"],
        filtered_categories=categories,
        mapping_url=mapping_url,
    )
    expected = []
    if categories is None or "main" in categories:
        expected.append(
            VersionedDependency(name="requests", manager="pip", version="*")
        )
    if conda_pip is not None:
        version, pip_category = conda_pip
        expected.append(
            VersionedDependency(name="pip", version=version, category=pip_category)
        )
    assert [d.model_dump() for d in spec.dependencies["linux-64"]] == [
        d.model_dump() for d in expected
    ]
    assert compute_content_hashes(spec, None)["linux-64"] == base_hash


@pytest.mark.parametrize("unconstrained", ["pip", "pip *"])
@pytest.mark.parametrize("categories", [None, {"main"}, {"dev"}, {"main", "dev"}])
def test_superseded_unconstrained_pip_is_irrelevant(
    mapping_url: str,
    tmp_path: Path,
    unconstrained: str,
    categories: set[str] | None,
) -> None:
    subsection = tmp_path / "subsection.yml"
    subsection.write_text("dependencies:\n  - pip: [requests]\n")
    superseded = tmp_path / "superseded.yml"
    superseded.write_text(f"category: dev\ndependencies:\n  - {unconstrained}\n")
    pinned = tmp_path / "pinned.yml"
    pinned.write_text("category: dev\ndependencies:\n  - pip=25.0.1\n")
    control, actual = (
        make_lock_spec(
            src_files=sources,
            platform_overrides=["linux-64"],
            filtered_categories=categories,
            mapping_url=mapping_url,
        )
        for sources in ([subsection, pinned], [subsection, superseded, pinned])
    )
    if categories == {"main"}:
        expected = [("*", "main")]
    else:
        expected = [("25.0.1.*", "dev" if categories == {"dev"} else "main")]
    assert [(d.version, d.category) for d in _conda_pip(actual, "linux-64")] == expected
    # The requirement that a later explicit requirement replaced never takes
    # effect, so adding it changes neither the dependencies nor the hash.
    assert [d.model_dump() for d in actual.dependencies["linux-64"]] == [
        d.model_dump() for d in control.dependencies["linux-64"]
    ]
    assert compute_content_hashes(actual, None) == compute_content_hashes(control, None)


_PIP_REQUIREMENTS = {
    "fallback": ("  - pip: [requests]\n", "*"),
    "pip": ("  - pip\n", ""),
    "pip *": ("  - pip *\n", "*"),
    "pip=25.0.1": ("  - pip=25.0.1\n", "25.0.1.*"),
}


def _expected_conda_pip(
    sequence: tuple[tuple[str, str], ...], categories: set[str] | None
) -> list[tuple[str, str]]:
    """The conda pip requirement that a sequence of source files must produce.

    Only the final explicit requirement and the presence of a fallback matter.
    A constrained final requirement beats the fallback and keeps the fallback's
    main membership. Otherwise the last requirement of any kind wins, as before.
    """

    def selected(category: str) -> bool:
        return categories is None or category in categories

    explicit = [(spec, category) for spec, category in sequence if spec != "fallback"]
    if explicit and explicit[-1][0] == "pip=25.0.1" and len(explicit) < len(sequence):
        category = explicit[-1][1]
        if selected(category):
            return [("25.0.1.*", "main" if selected("main") else category)]
        return [("*", "main")] if selected("main") else []
    spec, category = sequence[-1]
    if spec == "fallback":
        category = "main"
    return [(_PIP_REQUIREMENTS[spec][1], category)] if selected(category) else []


@pytest.mark.parametrize("categories", [None, {"main"}, {"dev"}, {"main", "dev"}])
def test_conda_pip_follows_from_final_requirements(
    mapping_url: str, tmp_path: Path, categories: set[str] | None
) -> None:
    requirements = [
        (spec, category) for spec in _PIP_REQUIREMENTS for category in ("main", "dev")
    ]
    for length in (1, 2, 3):
        for sequence in product(requirements, repeat=length):
            sources = []
            for index, (spec, category) in enumerate(sequence):
                source = tmp_path / f"{index}.yml"
                source.write_text(
                    f"category: {category}\ndependencies:\n{_PIP_REQUIREMENTS[spec][0]}"
                )
                sources.append(source)
            lock_spec = make_lock_spec(
                src_files=sources,
                platform_overrides=["linux-64"],
                filtered_categories=categories,
                mapping_url=mapping_url,
            )
            assert [
                (d.version, d.category) for d in _conda_pip(lock_spec, "linux-64")
            ] == _expected_conda_pip(sequence, categories), sequence


_DIRECT_REFERENCES: dict[str, tuple[str, type[Dependency]]] = {
    "url": ("pip @ https://example.invalid/pip-26.0-py3-none-any.whl", URLDependency),
    "vcs": ("pip @ git+https://example.invalid/pip.git", VCSDependency),
    "path": ("pip @ file:///tmp/pip-source", PathDependency),
}


@pytest.mark.parametrize("reference", list(_DIRECT_REFERENCES))
@pytest.mark.parametrize("pin_first", [True, False])
@pytest.mark.parametrize("category", ["main", "dev"])
@pytest.mark.parametrize("categories", [None, {"main"}, {"dev"}, {"main", "dev"}])
def test_direct_reference_is_the_final_explicit_pip(
    mapping_url: str,
    tmp_path: Path,
    reference: str,
    pin_first: bool,
    category: str,
    categories: set[str] | None,
) -> None:
    """A later URL, VCS or path reference is the explicit requirement.

    Aggregation must not replace it with a version pin it superseded. This does
    not make such references solvable; it keeps the actual final input.
    """
    requirement, dependency_type = _DIRECT_REFERENCES[reference]
    pinned = tmp_path / "pinned.yml"
    pinned.write_text("dependencies:\n  - pip=25.0.1\n")
    subsection = tmp_path / "subsection.yml"
    subsection.write_text("dependencies:\n  - pip: [requests]\n")
    project = tmp_path / "pyproject.toml"
    project.write_text(
        "[project]\nname = 'example'\nversion = '0'\n"
        + (
            f"dependencies = ['{requirement}']\n"
            if category == "main"
            else "dependencies = []\n[project.optional-dependencies]\n"
            f"{category} = ['{requirement}']\n"
        )
    )

    def lock(sources: list[Path], filtered: set[str] | None) -> LockSpecification:
        return make_lock_spec(
            src_files=sources,
            platform_overrides=["linux-64"],
            filtered_categories=filtered,
            mapping_url=mapping_url,
        )

    def selected(name: str) -> bool:
        return categories is None or name in categories

    [written] = [
        d
        for d in lock([project], None).dependencies["linux-64"]
        if (d.manager, d.name) == ("conda", "pip")
    ]
    assert isinstance(written, dependency_type)
    assert written.category == category

    control = lock([subsection, project], categories)
    actual = lock(
        [pinned, subsection, project] if pin_first else [subsection, pinned, project],
        categories,
    )
    conda_pip = [
        d
        for d in actual.dependencies["linux-64"]
        if (d.manager, d.name) == ("conda", "pip")
    ]
    if selected(category):
        # The direct reference is the final explicit requirement, with the
        # fallback's main membership when main is selected too.
        [final] = conda_pip
        assert type(final) is dependency_type
        assert final.model_dump() == {
            **written.model_dump(),
            "category": "main" if selected("main") else category,
        }
    elif selected("main"):
        # The reference is excluded, so the subsection's own requirement applies.
        assert [(type(d), d.category) for d in conda_pip] == [
            (VersionedDependency, "main")
        ]
        assert isinstance(conda_pip[0], VersionedDependency)
        assert conda_pip[0].version == "*"
    else:
        assert conda_pip == []
    # The superseded pin never takes effect, so it changes neither the
    # dependencies nor the content hash. Only its list position may differ.
    assert sorted(
        (d.model_dump() for d in actual.dependencies["linux-64"]),
        key=lambda d: (d["manager"], d["name"]),
    ) == sorted(
        (d.model_dump() for d in control.dependencies["linux-64"]),
        key=lambda d: (d["manager"], d["name"]),
    )
    assert compute_content_hashes(actual, None) == compute_content_hashes(control, None)


def test_category_filter_applies_after_explicit_last_wins(
    mapping_url: str, tmp_path: Path
) -> None:
    main = tmp_path / "main.yml"
    main.write_text("dependencies:\n  - python=3.11\n  - pip: [requests]\n")
    dev = tmp_path / "dev.yml"
    dev.write_text("category: dev\ndependencies:\n  - python=3.12\n")
    spec = make_lock_spec(
        src_files=[main, dev],
        platform_overrides=["linux-64"],
        filtered_categories={"main"},
        mapping_url=mapping_url,
    )
    names = [d.name for d in spec.dependencies["linux-64"]]
    # The dev python replaced the main python before filtering, so neither remains.
    assert "python" not in names
    assert [d.version for d in _conda_pip(spec, "linux-64")] == ["*"]


@pytest.mark.parametrize(
    "linux_pip, platform_independent",
    [("pip *", True), ("pip=25.0.1", False)],
)
def test_export_unifies_equal_explicit_and_fallback_pip(
    mapping_url: str, tmp_path: Path, linux_pip: str, platform_independent: bool
) -> None:
    source = tmp_path / "environment.yml"
    source.write_text(
        f"dependencies:\n  - {linux_pip}  # [linux]\n  - pip: [requests]\n"
    )
    spec = make_lock_spec(
        src_files=[source],
        platform_overrides=["linux-64", "osx-arm64"],
        mapping_url=mapping_url,
    )
    # Nothing hidden from serialisation may distinguish aggregated dependencies.
    for deps in spec.dependencies.values():
        assert [type(d).model_validate(d.model_dump()) for d in deps] == deps
    unified = unify_platform_independent_deps(spec.dependencies)
    pip_platforms = sorted(
        str(key.platform)
        for key in unified
        if (key.manager, key.name) == ("conda", "pip")
    )
    if platform_independent:
        assert pip_platforms == ["None"]
    else:
        assert pip_platforms == ["linux-64", "osx-arm64"]
