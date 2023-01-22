import pathlib

from typing import TYPE_CHECKING, List, Optional

import jinja2

from ruamel.yaml import YAML

from conda_lock.src_parser import SourceDependency, SourceFile
from conda_lock.src_parser.conda_common import conda_spec_to_versioned_dep
from conda_lock.src_parser.selectors import parse_selector_comment_for_dep


if TYPE_CHECKING:
    from ruamel.yaml.comments import CommentedMap, CommentedSeq


class UndefinedNeverFail(jinja2.Undefined):
    """
    Copied from https://github.com/conda/conda-build/blob/master/conda_build/jinja_context.py

    A class for Undefined jinja variables.
    This is even less strict than the default jinja2.Undefined class,
    because it permits things like {{ MY_UNDEFINED_VAR[:2] }} and
    {{ MY_UNDEFINED_VAR|int }}. This can mask lots of errors in jinja templates, so it
    should only be used for a first-pass parse, when you plan on running a 'strict'
    second pass later.
    Note:
        When using this class, any usage of an undefined variable in a jinja template is recorded
        in the (global) all_undefined_names class member.  Therefore, after jinja rendering,
        you can detect which undefined names were used by inspecting that list.
        Be sure to clear the all_undefined_names list before calling template.render().
    """

    all_undefined_names: List[Optional[str]] = []

    def __init__(  # type: ignore
        self,
        hint=None,
        obj=jinja2.utils.missing,
        name=None,
        exc=jinja2.exceptions.UndefinedError,
    ) -> None:
        jinja2.Undefined.__init__(self, hint, obj, name, exc)

    # Using any of these methods on an Undefined variable
    # results in another Undefined variable.
    # fmt: off
    __add__ = __radd__ = __mul__ = __rmul__ = __div__ = __rdiv__ = \
    __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = \
    __mod__ = __rmod__ = __pos__ = __neg__ = __call__ = \
    __getitem__ = __lt__ = __le__ = __gt__ = __ge__ = \
    __complex__ = __pow__ = __rpow__ = \
        lambda self, *args, **kwargs: self._return_undefined(self._undefined_name)  # noqa: E122
    # fmt: on

    # Accessing an attribute of an Undefined variable
    # results in another Undefined variable.
    def __getattr__(self, k: str) -> "UndefinedNeverFail":
        try:
            return object.__getattr__(self, k)  # type: ignore
        except AttributeError:
            return self._return_undefined(self._undefined_name + "." + k)  # type: ignore

    # Unlike the methods above, Python requires that these
    # few methods must always return the correct type
    __str__ = __repr__ = lambda self: self._return_value(str())  # type: ignore  # noqa: E731
    __unicode__ = lambda self: self._return_value("")  # noqa: E731
    __int__ = lambda self: self._return_value(0)  # type: ignore  # noqa: E731
    __float__ = lambda self: self._return_value(0.0)  # type: ignore  # noqa: E731
    __nonzero__ = lambda self: self._return_value(False)  # noqa: E731

    def _return_undefined(self, result_name: str) -> "UndefinedNeverFail":  # type: ignore
        # Record that this undefined variable was actually used.
        UndefinedNeverFail.all_undefined_names.append(self._undefined_name)
        return UndefinedNeverFail(
            hint=self._undefined_hint,
            obj=self._undefined_obj,
            name=result_name,
            exc=self._undefined_exception,
        )

    def _return_value(self, value=None):  # type: ignore
        # Record that this undefined variable was actually used.
        UndefinedNeverFail.all_undefined_names.append(self._undefined_name)
        return value


def parse_meta_yaml_file(meta_yaml_file: pathlib.Path) -> SourceFile:
    """Parse a simple meta-yaml file for dependencies.

    * This does not support multi-output files and will ignore all lines with
      selectors other than platform.
    """
    if not meta_yaml_file.exists():
        raise FileNotFoundError(f"{meta_yaml_file} not found")

    with meta_yaml_file.open("r") as fo:
        recipe = fo.read()

    t = jinja2.Template(recipe, undefined=UndefinedNeverFail)
    rendered = t.render()
    meta_yaml_data = YAML().load(rendered)

    channels = meta_yaml_data.mlget(["extra", "channels"], []).copy()
    dependencies: List[SourceDependency] = []

    def add_specs(group: "CommentedSeq", category: str) -> None:
        for idx, spec in enumerate(group):
            if spec is None:
                continue
            dep = conda_spec_to_versioned_dep(spec, category)
            dep.selectors.platform = parse_selector_comment_for_dep(group.ca, idx)
            dependencies.append(dep)

    def add_requirements_from_recipe_or_output(yaml_data: "CommentedMap") -> None:
        add_specs(yaml_data.mlget(["requirements", "host"], []), "main")
        add_specs(yaml_data.mlget(["requirements", "run"], []), "main")
        add_specs(yaml_data.mlget(["test", "requires"], []), "dev")

    add_requirements_from_recipe_or_output(meta_yaml_data)
    for output in meta_yaml_data.get("outputs", []):
        add_requirements_from_recipe_or_output(output)

    return SourceFile(
        file=meta_yaml_file,
        dependencies=dependencies,
        channels=channels,  # type: ignore
        platforms=set(),
    )
