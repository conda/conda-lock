import pathlib

import jinja2
import yaml

from conda_lock.src_parser import LockSpecification
from conda_lock.src_parser.selectors import filter_platform_selectors


class NullUndefined(jinja2.Undefined):
    def __getattr__(self, key):
        return ""

    # Using any of these methods on an Undefined variable
    # results in another Undefined variable.
    __add__ = (
        __radd__
    ) = (
        __mul__
    ) = (
        __rmul__
    ) = (
        __div__
    ) = (
        __rdiv__
    ) = (
        __truediv__
    ) = (
        __rtruediv__
    ) = (
        __floordiv__
    ) = (
        __rfloordiv__
    ) = (
        __mod__
    ) = (
        __rmod__
    ) = (
        __pos__
    ) = (
        __neg__
    ) = (
        __call__
    ) = (
        __getitem__
    ) = (
        __lt__
    ) = (
        __le__
    ) = (
        __gt__
    ) = (
        __ge__
    ) = (
        __complex__
    ) = __pow__ = __rpow__ = lambda self, *args, **kwargs: self._return_undefined(
        self._undefined_name
    )

    def _return_undefined(self, result_name):
        # Record that this undefined variable was actually used.
        return NullUndefined(
            hint=self._undefined_hint,
            obj=self._undefined_obj,
            name=result_name,
            exc=self._undefined_exception,
        )


def parse_meta_yaml_file(
    meta_yaml_file: pathlib.Path, platform: str
) -> LockSpecification:
    """Parse a simple meta-yaml file for dependencies.

    * This does not support multi-output files and will ignore all lines with selectors
    """
    if not meta_yaml_file.exists():
        raise FileNotFoundError(f"{meta_yaml_file} not found")

    with meta_yaml_file.open("r") as fo:
        filtered_recipe = "\n".join(
            filter_platform_selectors(fo.read(), platform=platform)
        )
        t = jinja2.Template(filtered_recipe, undefined=NullUndefined)
        rendered = t.render()
        meta_yaml_data = yaml.safe_load(rendered)
    channels = meta_yaml_data.get("extra", {}).get("channels", [])
    specs = []

    def add_spec(spec):
        if spec is None:
            return
        specs.append(spec)

    for s in meta_yaml_data.get("requirements", {}).get("host", []):
        add_spec(s)
    for s in meta_yaml_data.get("requirements", {}).get("run", []):
        add_spec(s)
    for s in meta_yaml_data.get("test", {}).get("requires", []):
        add_spec(s)

    return LockSpecification(specs=specs, channels=channels, platform=platform)
