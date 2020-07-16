import pathlib
import sys

import yaml

from conda_lock.src_parser import LockSpecification
from conda_lock.src_parser.selectors import filter_platform_selectors


def parse_environment_file(
    environment_file: pathlib.Path, platform: str
) -> LockSpecification:
    if not environment_file.exists():
        raise FileNotFoundError(f"{environment_file} not found")

    with environment_file.open("r") as fo:
        filtered_content = "\n".join(
            filter_platform_selectors(fo.read(), platform=platform)
        )
        env_yaml_data = yaml.safe_load(filtered_content)
    # TODO: we basically ignore most of the fields for now.
    #       notable pip deps are just skipped below
    specs = env_yaml_data["dependencies"]
    channels = env_yaml_data.get("channels", [])

    # Split out any sub spec sections from the dependencies mapping
    mapping_specs = [x for x in specs if not isinstance(x, str)]
    specs = [x for x in specs if isinstance(x, str)]

    # Print a warning if there are pip specs in the dependencies
    for mapping_spec in mapping_specs:
        if "pip" in mapping_spec:
            print(
                (
                    "Warning, found pip deps not included in the lock file! You'll need to install "
                    "them separately"
                ),
                file=sys.stderr,
            )

    return LockSpecification(specs=specs, channels=channels, platform=platform)
