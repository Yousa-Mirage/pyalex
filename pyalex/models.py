"""OpenAlex response and entity models."""

import html
from typing import Any
from typing import Literal
from typing import NotRequired
from typing import TypedDict
from typing import cast
from typing import overload
from typing import override

from pyalex.config import config
from pyalex.http import OpenAlexAuth
from pyalex.http import RateLimit
from pyalex.http import _get_client
from pyalex.http import _send


class Meta(TypedDict):
    """Metadata returned alongside a list of OpenAlex entities."""

    count: int
    db_response_time_ms: int
    page: NotRequired[int]
    per_page: NotRequired[int]
    next_cursor: NotRequired[str | None]
    groups_count: NotRequired[int]
    ratelimit: NotRequired[RateLimit]


def invert_abstract(inv_index: dict[str, list[int]] | None) -> str | None:
    """Turn an OpenAlex inverted abstract index into readable text."""
    if inv_index is not None:
        positions = sorted(
            (position, word) for word, pos in inv_index.items() for position in pos
        )
        return html.unescape(" ".join(word for _, word in positions))


class OpenAlexEntity(dict[str, Any]):
    """Base class for OpenAlex entities."""


class OpenAlexResponseList[T: OpenAlexEntity](list[T]):
    """List of typed entities with response metadata."""

    def __init__(
        self,
        results: list[dict[str, Any]],
        meta: Meta | None = None,
        resource_class: type[T] | None = None,
    ) -> None:
        self.resource_class: type[T] = resource_class or cast(type[T], OpenAlexEntity)
        self.meta: Meta | None = meta
        super().__init__([self.resource_class(ent) for ent in results])


class GroupByBucket(OpenAlexEntity):
    """One bucket of a ``group_by`` response."""

    @overload
    def __getitem__(self, key: Literal["key"]) -> str: ...
    @overload
    def __getitem__(self, key: Literal["key_display_name"]) -> str: ...
    @overload
    def __getitem__(self, key: Literal["count"]) -> int: ...
    @overload
    def __getitem__(self, key: str) -> Any: ...
    @override
    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key)


class BaseContent:
    """Base class for downloadable OpenAlex work content."""

    def __init__(self, key: str) -> None:
        self.key = key

    @override
    def __repr__(self) -> str:
        return f"Content(key='{self.key}')"

    @property
    def url(self) -> str:
        return f"https://content.openalex.org/works/{self.key}"

    def get(self) -> bytes:
        response = _send(_get_client(), self.url, auth=OpenAlexAuth(config))
        response.raise_for_status()
        return response.content

    def download(self, filepath: str) -> None:
        with open(filepath, "wb") as file:
            file.write(self.get())


class PDF(BaseContent):
    """PDF content for a work."""

    @property
    @override
    def url(self) -> str:
        return f"https://content.openalex.org/works/{self.key}.pdf"


class TEI(BaseContent):
    """TEI XML content for a work."""

    @property
    @override
    def url(self) -> str:
        return f"https://content.openalex.org/works/{self.key}.grobid-xml"


class Work(OpenAlexEntity):
    """Class representing a work entity in OpenAlex."""

    @overload
    def __getitem__(self, key: Literal["abstract"]) -> str | None: ...
    @overload
    def __getitem__(
        self, key: Literal["abstract_inverted_index"]
    ) -> dict[str, list[int]] | None: ...
    @overload
    def __getitem__(self, key: Literal["id"]) -> str: ...
    @overload
    def __getitem__(self, key: Literal["doi"]) -> str | None: ...
    @overload
    def __getitem__(self, key: Literal["display_name"]) -> str | None: ...
    @overload
    def __getitem__(self, key: Literal["publication_year"]) -> int | None: ...
    @overload
    def __getitem__(self, key: Literal["cited_by_count"]) -> int: ...
    @overload
    def __getitem__(self, key: Literal["type"]) -> str: ...
    @overload
    def __getitem__(self, key: Literal["open_access"]) -> dict[str, Any]: ...
    @overload
    def __getitem__(self, key: Literal["referenced_works"]) -> list[str]: ...
    @overload
    def __getitem__(self, key: Literal["authorships"]) -> list[dict[str, Any]]: ...
    @overload
    def __getitem__(self, key: str) -> Any: ...
    @override
    def __getitem__(self, key: str) -> Any:
        if key == "abstract":
            return invert_abstract(self["abstract_inverted_index"])
        return super().__getitem__(key)

    @property
    def pdf(self) -> PDF:
        return PDF(self["id"].split("/")[-1])

    @property
    def tei(self) -> TEI:
        return TEI(self["id"].split("/")[-1])


class Author(OpenAlexEntity):
    """Class representing an author entity in OpenAlex."""


class Source(OpenAlexEntity):
    """Class representing a source entity in OpenAlex."""


class Institution(OpenAlexEntity):
    """Class representing an institution entity in OpenAlex."""


class Domain(OpenAlexEntity):
    """Class representing a domain entity in OpenAlex."""


class Field(OpenAlexEntity):
    """Class representing a field entity in OpenAlex."""


class Subfield(OpenAlexEntity):
    """Class representing a subfield entity in OpenAlex."""


class Topic(OpenAlexEntity):
    """Class representing a topic entity in OpenAlex."""


class Publisher(OpenAlexEntity):
    """Class representing a publisher entity in OpenAlex."""


class Funder(OpenAlexEntity):
    """Class representing a funder entity in OpenAlex."""


class Award(OpenAlexEntity):
    """Class representing an award entity in OpenAlex."""


class Keyword(OpenAlexEntity):
    """Class representing a keyword entity in OpenAlex."""


class Autocomplete(OpenAlexEntity):
    """Class representing an autocomplete suggestion."""
