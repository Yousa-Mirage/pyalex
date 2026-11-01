"""Package configuration and version lookup."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from typing import TYPE_CHECKING
from typing import Any

try:
    __version__ = version("pyalex")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"


class AlexConfig(dict[str, Any]):
    """Mutable configuration for OpenAlex API requests."""

    email: str | None
    api_key: str | None
    user_agent: str
    openalex_url: str
    timeout: float
    max_retries: int
    retry_backoff_factor: float
    retry_http_codes: list[int]

    def __init__(
        self,
        email: str | None = None,
        api_key: str | None = None,
        user_agent: str | None = None,
        openalex_url: str = "https://api.openalex.org",
        timeout: float = 60.0,
        max_retries: int = 0,
        retry_backoff_factor: float = 0.1,
        retry_http_codes: list[int] | None = None,
    ) -> None:
        if user_agent is None:
            user_agent = f"pyalex/{__version__}"
        if retry_http_codes is None:
            retry_http_codes = [429, 500, 503]

        super().__init__(
            email=email,
            api_key=api_key,
            user_agent=user_agent,
            openalex_url=openalex_url,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff_factor=retry_backoff_factor,
            retry_http_codes=retry_http_codes,
        )

    if not TYPE_CHECKING:

        def __getattr__(self, key: str) -> Any:
            try:
                return super().__getitem__(key)
            except KeyError:
                raise AttributeError(key) from None

        def __setattr__(self, key: str, value: Any) -> None:
            super().__setitem__(key, value)


config = AlexConfig(email=None, api_key=None)
