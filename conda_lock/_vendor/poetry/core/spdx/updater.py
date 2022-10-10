import json
import os

from io import open
from typing import Any
from typing import Dict
from typing import Optional


try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen


class Updater:

    BASE_URL = "https://raw.githubusercontent.com/spdx/license-list-data/master/json/"

    def __init__(self, base_url=BASE_URL):  # type: (str) -> None
        self._base_url = base_url

    def dump(self, file=None):  # type: (Optional[str]) -> None
        if file is None:
            file = os.path.join(os.path.dirname(__file__), "data", "licenses.json")

        licenses_url = self._base_url + "licenses.json"

        with open(file, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(self.get_licenses(licenses_url), indent=2, sort_keys=True)
            )

    def get_licenses(self, url):  # type: (str) -> Dict[str, Any]
        licenses = {}
        with urlopen(url) as r:
            data = json.loads(r.read().decode())

        for info in data["licenses"]:
            licenses[info["licenseId"]] = [
                info["name"],
                info["isOsiApproved"],
                info["isDeprecatedLicenseId"],
            ]

        return licenses
