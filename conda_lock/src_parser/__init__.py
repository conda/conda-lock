import hashlib
import json

from typing import List, Optional

from conda_lock.virtual_package import FakeRepoData


class LockSpecification:
    def __init__(
        self,
        specs: List[str],
        channels: List[str],
        platform: str,
        virtual_package_repo: Optional[FakeRepoData] = None,
    ):
        self.specs = specs
        self.channels = channels
        self.platform = platform
        self.virtual_package_repo = virtual_package_repo

    def input_hash(self) -> str:
        data: dict = {
            "channels": self.channels,
            "platform": self.platform,
            "specs": sorted(self.specs),
        }
        if self.virtual_package_repo is not None:
            vpr_data = self.virtual_package_repo.all_repodata
            data["virtual_package_hash"] = {
                "noarch": vpr_data.get("noarch", {}),
                self.platform: vpr_data.get(self.platform, {}),
            }

        env_spec = json.dumps(data, sort_keys=True)
        return hashlib.sha256(env_spec.encode("utf-8")).hexdigest()
