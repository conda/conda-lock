import hashlib
import json

from typing import List


class LockSpecification:
    def __init__(self, specs: List[str], channels: List[str], platform: str):
        self.specs = specs
        self.channels = channels
        self.platform = platform

    def input_hash(self) -> str:
        env_spec = json.dumps(
            {
                "channels": self.channels,
                "platform": self.platform,
                "specs": sorted(self.specs),
            },
            sort_keys=True,
        )
        return hashlib.sha256(env_spec.encode("utf-8")).hexdigest()
