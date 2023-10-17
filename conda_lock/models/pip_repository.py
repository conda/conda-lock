from hashlib import sha256
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict


class PipRepository(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str

    @classmethod
    def from_string(cls, url: str) -> "PipRepository":
        return PipRepository(url=url)

    @property
    def base_url(self) -> str:
        """The base URL of the pip repository, without a URL path."""
        full_url = urlparse(self.url)
        return full_url.scheme + "://" + full_url.netloc

    @property
    def stripped_base_url(self) -> str:
        """The base URL of the pip repository, without any basic auth."""
        base_url = urlparse(self.base_url)
        return urlunparse(base_url._replace(netloc=base_url.netloc.split("@", 1)[-1]))

    @property
    def name(self) -> str:
        """Poetry solver requires a name for each repository.

        We use this to match solver results back to their relevant
        repository.
        """
        sha = sha256()
        sha.update(self.url.encode("utf-8"))
        return sha.hexdigest()

    def normalize_solver_url(self, solver_url: str) -> str:
        """Normalize the URL returned by Poetry's solver.

        Poetry doesn't return URLs with URL-based basic Auth, because it gets converted to
        header-based Basic Auth. Because of this, we have to add the auth back in here.
        """
        if not solver_url.startswith(self.stripped_base_url):
            # The resolved package URL is at a different host to the repository
            return solver_url
        return solver_url.replace(self.stripped_base_url, self.base_url, 1)
