import logging
from collections.abc import Callable
from collections.abc import Iterator
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import Literal
from typing import Self
from typing import cast
from typing import overload
from typing import override
from urllib.parse import quote_plus
from urllib.parse import urlunparse

import httpx2

from pyalex.config import AlexConfig as AlexConfig
from pyalex.config import config
from pyalex.http import CreditsExhaustedError as CreditsExhaustedError
from pyalex.http import OpenAlexAuth as OpenAlexAuth
from pyalex.http import QueryError as QueryError
from pyalex.http import RateLimit as RateLimit
from pyalex.http import _extract_ratelimit
from pyalex.http import _get_client
from pyalex.http import _send
from pyalex.models import PDF as PDF
from pyalex.models import TEI as TEI
from pyalex.models import Author as Author
from pyalex.models import Autocomplete as Autocomplete
from pyalex.models import Award as Award
from pyalex.models import Domain as Domain
from pyalex.models import Field as Field
from pyalex.models import Funder as Funder
from pyalex.models import GroupByBucket as GroupByBucket
from pyalex.models import Institution as Institution
from pyalex.models import Keyword as Keyword
from pyalex.models import Meta
from pyalex.models import OpenAlexEntity as OpenAlexEntity
from pyalex.models import OpenAlexResponseList as OpenAlexResponseList
from pyalex.models import Publisher as Publisher
from pyalex.models import Source as Source
from pyalex.models import Subfield as Subfield
from pyalex.models import Topic as Topic
from pyalex.models import Work as Work
from pyalex.models import invert_abstract as invert_abstract


class or_(dict[str, Any]):
    """Logical OR expression class."""


class _LogicalExpression:
    """Base class for logical filter expressions."""

    token: str | None = None

    def __init__(self, value: Any) -> None:
        self.value = value

    @override
    def __str__(self) -> str:
        return f"{self.token}{self.value}"


class not_(_LogicalExpression):
    token = "!"


class gt_(_LogicalExpression):
    token = ">"


class lt_(_LogicalExpression):
    token = "<"


def _quote_oa_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, _LogicalExpression) and isinstance(value.value, str):
        return type(value)(quote_plus(value.value))
    if isinstance(value, str):
        return quote_plus(value)
    return value


def _flatten_kv(
    data: Any, prefix: str | None = None, logical: Literal["+", "|"] = "+"
) -> str:
    if prefix is None and not isinstance(data, dict):
        raise ValueError("prefix should be set if d is not a dict")
    if isinstance(data, dict):
        logical_subd = "|" if isinstance(data, or_) else logical
        return ",".join(
            _flatten_kv(
                value,
                prefix=f"{prefix}.{key}" if prefix else f"{key}",
                logical=logical_subd,
            )
            for key, value in data.items()
        )
    if isinstance(data, list):
        return f"{prefix}:{logical.join(str(_quote_oa_value(item)) for item in data)}"
    return f"{prefix}:{_quote_oa_value(data)}"


def _params_merge(params: dict[str, Any], add_params: dict[str, Any]) -> None:
    for key, value in add_params.items():
        if key in params and isinstance(params[key], dict) and isinstance(value, dict):
            _params_merge(params[key], value)
        elif (
            key in params
            and not isinstance(params[key], list)
            and isinstance(value, list)
        ):
            params[key] = [params[key], *value]
        elif (
            key in params
            and isinstance(params[key], list)
            and not isinstance(value, list)
        ):
            params[key] = [*params[key], value]
        elif key in params:
            params[key] = [params[key], value]
        else:
            params[key] = value


def _wrap_values_nested_dict(
    data: dict[str, Any], func: Callable[[Any], Any]
) -> dict[str, Any]:
    for key, value in data.items():
        if isinstance(value, dict):
            data[key] = _wrap_values_nested_dict(value, func)
        elif isinstance(value, list):
            data[key] = [func(item) for item in value]
        else:
            data[key] = func(value)
    return data


logger = logging.getLogger("pyalex")


_client: httpx2.Client | None = None


class Paginator[T: OpenAlexEntity]:
    """Paginator for OpenAlex API results.

    Attributes
    ----------
    VALUE_CURSOR_START : str
        Starting value for cursor pagination.
    VALUE_NUMBER_START : int
        Starting value for page pagination.

    Parameters
    ----------
    endpoint_class : class
        Class of the endpoint to paginate.
    method : str, optional
        Pagination method ('cursor' or 'page').
    value : any, optional
        Starting value for pagination.
    per_page : int, optional
        Number of results per page.
    n_max : int, optional
        Maximum number of results.
    """

    VALUE_CURSOR_START: Final[str] = "*"
    VALUE_NUMBER_START: Final[int] = 1

    def __init__(
        self,
        endpoint_class: "BaseOpenAlex[T]",
        method: Literal["cursor", "page"] = "cursor",
        value: str | int | None = None,
        per_page: int | None = None,
        n_max: int | None = None,
    ) -> None:
        self.method: Literal["cursor", "page"] = method
        self.endpoint_class = endpoint_class
        self.value = value
        self.per_page = per_page
        self.n_max = n_max
        self.n = 0

        self._next_value: str | int | None = value
        self._client = _get_client()

    def __iter__(self) -> Iterator[OpenAlexResponseList[T]]:
        return self

    def _is_max(self) -> bool:
        return bool(self.n_max and self.n >= self.n_max)

    def __next__(self) -> OpenAlexResponseList[T]:
        if self._next_value is None or self._is_max():
            raise StopIteration

        if self.method == "cursor":
            self.endpoint_class._add_params("cursor", self._next_value)
        elif self.method == "page":
            self.endpoint_class._add_params("page", self._next_value)
        else:
            raise ValueError("Method should be 'cursor' or 'page'")

        if self.per_page is not None and (
            not isinstance(self.per_page, int)
            or (self.per_page < 1 or self.per_page > 200)
        ):
            raise ValueError("per_page should be a integer between 1 and 200")

        if self.per_page is not None:
            self.endpoint_class._add_params("per-page", self.per_page)

        r = self.endpoint_class._get_from_url(self.endpoint_class.url, self._client)
        assert isinstance(r, OpenAlexResponseList)

        if self.method == "cursor":
            assert r.meta is not None
            self._next_value = r.meta["next_cursor"]

        if self.method == "page":
            # Stop at the last page instead of asking for one page too many
            # (that extra request is a guaranteed 400 when ``sample`` is used).
            assert r.meta is not None
            current_page = r.meta.get("page", 1)
            per_page = r.meta.get("per_page") or self.per_page
            total = r.meta.get("count", 0)

            if per_page is None:
                self._next_value = None
            else:
                self._next_value = (
                    current_page + 1 if current_page * per_page < total else None
                )

        self.n = self.n + len(r)

        return r


def _fetch_entities[ResponseT: OpenAlexEntity](
    url: str,
    *,
    params: str | list[str] | dict[str, Any] | None,
    resource_class: type[ResponseT],
    client: httpx2.Client | None = None,
) -> ResponseT | OpenAlexResponseList[ResponseT]:
    """Perform a GET request and parse the OpenAlex response.

    Shared by the entity collections and by the type-less autocomplete
    endpoint, which does not follow the ``BaseOpenAlex`` contract.

    Parameters
    ----------
    url : str
        URL to request.
    params : str | list[str] | dict[str, Any] | None
        The query parameters of the caller. Only used to detect ``group-by``.
    resource_class : type[OpenAlexEntity]
        Class used to wrap every record of the response.
    client : httpx2.Client, optional
        Client to reuse. The shared client is used when omitted.

    Returns
    -------
    OpenAlexEntity | OpenAlexResponseList
        A single entity, or a list of entities with metadata.
    """
    if client is None:
        client = _get_client()

    logger.debug(f"GET request to URL: {url}")

    res = _send(client, url, auth=OpenAlexAuth(config))

    if res.status_code == 400:
        payload = res.json()
        if isinstance(payload["error"], str) and "query parameters" in payload["error"]:
            raise QueryError(payload["message"])
    if res.status_code == 401 and "API key" in res.json()["error"]:
        raise QueryError(f"{res.json()['error']}. Did you configure a valid API key?")

    res.raise_for_status()
    res_json = res.json()

    meta: Any = res_json.get("meta")
    ratelimit = _extract_ratelimit(res)
    if meta is not None and ratelimit:
        meta = {**meta, "ratelimit": ratelimit}
    response_meta = cast(Meta | None, meta)

    if params and "group-by" in params:
        # ``group_by`` rows are buckets, not entities of type ResponseT.
        return cast(
            OpenAlexResponseList[ResponseT],
            OpenAlexResponseList(res_json["group_by"], response_meta, GroupByBucket),
        )
    elif "results" in res_json:
        return OpenAlexResponseList(res_json["results"], response_meta, resource_class)
    elif "id" in res_json:
        return resource_class(res_json)
    else:
        raise ValueError("Unknown response format")


class BaseOpenAlex[T: OpenAlexEntity]:
    """Base class for OpenAlex objects.

    Parameters
    ----------
    params : dict, optional
        Parameters for the API request.
    """

    resource_class: type[T] = cast(type[T], OpenAlexEntity)

    def __init__(self, params: str | list[str] | dict[str, Any] | None = None) -> None:
        self.params = params

    # ``__getattr__`` always raises, so it is hidden from type checkers: ty then
    # reports attribute typos (e.g. ``Works().filtr(...)``) instead of silently
    # falling back to ``Any``. Runtime behaviour is unchanged.
    if not TYPE_CHECKING:

        def __getattr__(self, key: str) -> Any:
            if key == "groupby":
                raise AttributeError(
                    "Object has no attribute 'groupby'. Did you mean 'group_by'?"
                )

            if key == "filter_search":
                raise AttributeError(
                    "Object has no attribute 'filter_search'. "
                    "Did you mean 'search_filter'?"
                )

            if key == "query":
                raise AttributeError(
                    "Object has no attribute 'query'. "
                    "Use Works().similar() to find similar works."
                )

            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{key}'"
            )

    @overload
    def __getitem__(self, record_id: str) -> T: ...
    @overload
    def __getitem__(self, record_id: list[str]) -> OpenAlexResponseList[T]: ...

    def __getitem__(self, record_id: str | list[str]) -> T | OpenAlexResponseList[T]:
        if isinstance(record_id, list):
            if len(record_id) > 100:
                raise ValueError("OpenAlex does not support more than 100 ids")

            return self.filter_or(openalex_id=record_id).get(per_page=len(record_id))
        elif isinstance(record_id, str):
            self.params = record_id
            return self._get_from_url(self.url)
        else:
            raise ValueError("record_id should be a string or a list of strings")

    def _url_query(self) -> str | None:
        if isinstance(self.params, list):
            ids = "|".join(str(_quote_oa_value(i)) for i in self.params)
            return f"filter=openalex_id:{ids}"
        elif isinstance(self.params, dict):
            l_params = []
            for k, v in self.params.items():
                if v is None:
                    pass
                elif isinstance(v, list):
                    joined = ",".join(str(_quote_oa_value(i)) for i in v)
                    l_params.append(f"{k}={joined}")
                elif k in ["filter", "sort"]:
                    l_params.append(f"{k}={_flatten_kv(v)}")
                else:
                    l_params.append(f"{k}={_quote_oa_value(v)}")

            if l_params:
                return "&".join(l_params)
            return None

        else:
            return ""

    @property
    def url(self) -> str:
        """Return the URL for the API request.

        The URL doens't include the identification, authentication,
        and pagination parameters.


        Returns
        -------
        str
            URL for the API request.
        """
        base_path = self.__class__.__name__.lower()

        if isinstance(self.params, str):
            path = f"{base_path}/{_quote_oa_value(self.params)}"
            query = ""
        else:
            path = base_path
            query = self._url_query() or ""

        return urlunparse(("https", "api.openalex.org", path, "", query, ""))

    def count(self) -> int:
        """Get the count of results.

        Returns
        -------
        int
            Count of results.
        """
        r = self.get(per_page=1)
        assert r.meta is not None
        return r.meta["count"]

    def _get_from_url(
        self, url: str, client: httpx2.Client | None = None
    ) -> T | OpenAlexResponseList[T]:
        return _fetch_entities(
            url,
            params=self.params,
            resource_class=self.resource_class,
            client=client,
        )

    def get(
        self,
        page: int | None = None,
        per_page: int | None = None,
        cursor: str | None = None,
    ) -> OpenAlexResponseList[T]:
        if per_page is not None and (
            not isinstance(per_page, int) or (per_page < 1 or per_page > 200)
        ):
            raise ValueError("per_page should be an integer between 1 and 200")

        if not isinstance(self.params, (str, list)):
            self._add_params("per-page", per_page)
            self._add_params("page", page)
            self._add_params("cursor", cursor)

        resp_list = self._get_from_url(self.url)
        assert isinstance(resp_list, OpenAlexResponseList)
        return resp_list

    def paginate(
        self,
        method: Literal["cursor", "page"] = "cursor",
        page: int = 1,
        per_page: int | None = None,
        cursor: str = "*",
        n_max: int | None = 10000,
    ) -> Paginator[T]:
        """Paginate results from the API.

        Parameters
        ----------
        method : str, optional
            Pagination method ('cursor' or 'page').
        page : int, optional
            Page number for pagination.
        per_page : int, optional
            Number of results per page.
        cursor : str, optional
            Cursor for pagination.
        n_max : int, optional
            Maximum number of results.

        Returns
        -------
        Paginator
            Paginator object.
        """
        if method == "cursor":
            if isinstance(self.params, dict) and self.params.get("sample"):
                raise ValueError("method should be 'page' when using sample")
            value = cursor
        elif method == "page":
            value = page
        else:
            raise ValueError("Method should be 'cursor' or 'page'")

        return Paginator(
            self, method=method, value=value, per_page=per_page, n_max=n_max
        )

    def random(self) -> T:
        """Get a random result.

        Returns
        -------
        OpenAlexEntity
            Random result.
        """
        return self.__getitem__("random")

    def _add_params(
        self, argument: str, new_params: Any, raise_if_exists: bool = False
    ) -> None:
        """Add parameters to the API request.

        Parameters
        ----------
        argument : str
            Parameter name.
        new_params : any
            Parameter value.
        raise_if_exists : bool, optional
            Whether to raise an error if the parameter already exists.
        """
        if raise_if_exists:
            raise NotImplementedError("raise_if_exists is not implemented")

        if self.params is None:
            self.params = {argument: new_params}
        elif isinstance(self.params, dict):
            if argument in self.params and isinstance(self.params[argument], dict):
                _params_merge(self.params[argument], new_params)
            else:
                self.params[argument] = new_params
        else:
            # ``params`` is a record id (or a list of ids) because the object was
            # used as ``Works()["W123"]`` or ``Works().random()``. Mixing query
            # parameters into it would be silently ignored, so fail loudly.
            raise QueryError(
                "Cannot add query parameters to a single-entity request. "
                "Build a new query object instead, e.g. Works().filter(...)."
            )

        logger.debug(f"Params updated: {self.params}")

    def filter(self, **kwargs: Any) -> Self:
        """Add filter parameters to the API request.

        Parameters
        ----------
        **kwargs : dict
            Filter parameters.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("filter", kwargs)
        return self

    def filter_and(self, **kwargs: Any) -> Self:
        """Add AND filter parameters to the API request.

        Parameters
        ----------
        **kwargs : dict
            Filter parameters.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        return self.filter(**kwargs)

    def filter_or(self, **kwargs: Any) -> Self:
        """Add OR filter parameters to the API request.

        Parameters
        ----------
        **kwargs : dict
            Filter parameters.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("filter", or_(kwargs), raise_if_exists=False)
        return self

    def filter_not(self, **kwargs: Any) -> Self:
        """Add NOT filter parameters to the API request.

        Parameters
        ----------
        **kwargs : dict
            Filter parameters.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("filter", _wrap_values_nested_dict(kwargs, not_))
        return self

    def filter_gt(self, **kwargs: Any) -> Self:
        """Add greater than filter parameters to the API request.

        Parameters
        ----------
        **kwargs : dict
            Filter parameters.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("filter", _wrap_values_nested_dict(kwargs, gt_))
        return self

    def filter_lt(self, **kwargs: Any) -> Self:
        """Add less than filter parameters to the API request.

        Parameters
        ----------
        **kwargs : dict
            Filter parameters.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("filter", _wrap_values_nested_dict(kwargs, lt_))
        return self

    def search_filter(self, **kwargs: Any) -> Self:
        """Add search filter parameters to the API request.

        Parameters
        ----------
        **kwargs : dict
            Filter parameters.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("filter", {f"{k}.search": v for k, v in kwargs.items()})
        return self

    def sort(self, **kwargs: Any) -> Self:
        """Add sort parameters to the API request.

        Parameters
        ----------
        **kwargs : dict
            Sort parameters.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("sort", kwargs)
        return self

    def group_by(self, group_key: str) -> "GroupByQuery[T]":
        """Add group-by parameters to the API request.

        The returned object is not a :class:`BaseOpenAlex` because ``get()``
        returns buckets instead of entities.

        Parameters
        ----------
        group_key : str
            Group-by key.

        Returns
        -------
        GroupByQuery
            Typed view returning ``GroupByBucket`` objects.
        """
        self._add_params("group-by", group_key)
        return GroupByQuery(self)

    def search(self, s: str) -> Self:
        """Add search parameters to the API request.

        Parameters
        ----------
        s : str
            Search string.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("search", s)
        return self

    def sample(self, n: int, seed: int | None = None) -> Self:
        """Add sample parameters to the API request.

        Parameters
        ----------
        n : int
            Number of samples.
        seed : int, optional
            Seed for sampling.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("sample", n)
        self._add_params("seed", seed)
        return self

    def select(self, s: str | list[str]) -> Self:
        """Add select parameters to the API request.

        Parameters
        ----------
        s : str
            Select string.

        Returns
        -------
        BaseOpenAlex
            Updated object.
        """
        self._add_params("select", s)
        return self

    def autocomplete(self, s: str) -> OpenAlexResponseList[T]:
        """Return the OpenAlex autocomplete results.

        Parameters
        ----------
        s : str
            String to autocomplete.

        Returns
        -------
        OpenAlexResponseList
            List of autocomplete results.
        """

        self._add_params("q", s)

        resp_list = self._get_from_url(
            urlunparse(
                (
                    "https",
                    "api.openalex.org",
                    f"autocomplete/{self.__class__.__name__.lower()}",
                    "",
                    self._url_query() or "",
                    "",
                )
            )
        )
        assert isinstance(resp_list, OpenAlexResponseList)
        return resp_list


# The API


class Works(BaseOpenAlex[Work]):
    """Class representing a collection of work entities in OpenAlex."""

    resource_class = Work

    def similar(self, text: str) -> Self:
        """Find similar works using semantic search.

        Uses the search.semantic parameter for AI-powered semantic search.

        Parameters
        ----------
        text : str
            Query text to find similar works.

        Returns
        -------
        Works
            Updated object.
        """
        self._add_params("search.semantic", text)
        return self


class Authors(BaseOpenAlex[Author]):
    """Class representing a collection of author entities in OpenAlex."""

    resource_class = Author


class Sources(BaseOpenAlex[Source]):
    """Class representing a collection of source entities in OpenAlex."""

    resource_class = Source


class Institutions(BaseOpenAlex[Institution]):
    """Class representing a collection of institution entities in OpenAlex."""

    resource_class = Institution


class Domains(BaseOpenAlex[Domain]):
    """Class representing a collection of domain entities in OpenAlex."""

    resource_class = Domain


class Fields(BaseOpenAlex[Field]):
    """Class representing a collection of field entities in OpenAlex."""

    resource_class = Field


class Subfields(BaseOpenAlex[Subfield]):
    """Class representing a collection of subfield entities in OpenAlex."""

    resource_class = Subfield


class Topics(BaseOpenAlex[Topic]):
    """Class representing a collection of topic entities in OpenAlex."""

    resource_class = Topic


class Publishers(BaseOpenAlex[Publisher]):
    """Class representing a collection of publisher entities in OpenAlex."""

    resource_class = Publisher


class Funders(BaseOpenAlex[Funder]):
    """Class representing a collection of funder entities in OpenAlex."""

    resource_class = Funder


class Awards(BaseOpenAlex[Award]):
    """Class representing a collection of award entities in OpenAlex."""

    resource_class = Award


class Keywords(BaseOpenAlex[Keyword]):
    """Class representing a collection of keyword entities in OpenAlex."""

    resource_class = Keyword


class autocompletes:
    """Class to autocomplete without being based on the type of entity.

    This is intentionally *not* a ``BaseOpenAlex`` subclass: indexing an
    autocomplete endpoint returns a list of suggestions for the ``str`` case,
    which would violate the ``__getitem__`` contract of entity collections.
    """

    resource_class: type[Autocomplete] = Autocomplete

    def __getitem__(self, key: str) -> OpenAlexResponseList[Autocomplete]:
        return autocomplete(key)


def autocomplete(s: str) -> OpenAlexResponseList[Autocomplete]:
    """Autocomplete with any type of entity.

    Parameters
    ----------
    s : str
        String to autocomplete.

    Returns
    -------
    OpenAlexResponseList
        List of autocomplete results.
    """
    url = urlunparse(
        (
            "https",
            "api.openalex.org",
            "autocomplete",
            "",
            f"q={quote_plus(s)}",
            "",
        )
    )
    return cast(
        OpenAlexResponseList[Autocomplete],
        _fetch_entities(url, params=None, resource_class=Autocomplete),
    )


# aliases
People = Authors
Journals = Sources


class GroupByQuery[T: OpenAlexEntity]:
    """A ``group_by`` query whose results are buckets instead of entities.

    Returned by :meth:`BaseOpenAlex.group_by`. It deliberately does not inherit
    from ``BaseOpenAlex``: the rows are not entities of type ``T``, and
    pretending otherwise would make ``get()`` return the wrong element type.
    """

    def __init__(self, query: BaseOpenAlex[T]) -> None:
        self._query = query

    @property
    def url(self) -> str:
        """URL of the underlying request."""
        return self._query.url

    def filter(self, **kwargs: Any) -> Self:
        self._query.filter(**kwargs)
        return self

    def filter_and(self, **kwargs: Any) -> Self:
        self._query.filter_and(**kwargs)
        return self

    def filter_or(self, **kwargs: Any) -> Self:
        self._query.filter_or(**kwargs)
        return self

    def filter_not(self, **kwargs: Any) -> Self:
        self._query.filter_not(**kwargs)
        return self

    def filter_gt(self, **kwargs: Any) -> Self:
        self._query.filter_gt(**kwargs)
        return self

    def filter_lt(self, **kwargs: Any) -> Self:
        self._query.filter_lt(**kwargs)
        return self

    def search_filter(self, **kwargs: Any) -> Self:
        self._query.search_filter(**kwargs)
        return self

    def sort(self, **kwargs: Any) -> Self:
        self._query.sort(**kwargs)
        return self

    def select(self, s: str | list[str]) -> Self:
        self._query.select(s)
        return self

    def get(self) -> OpenAlexResponseList[GroupByBucket]:
        """Return the groups.

        Returns
        -------
        OpenAlexResponseList[GroupByBucket]
            One bucket per group, each with ``key``, ``key_display_name`` and
            ``count``.
        """
        return cast(OpenAlexResponseList[GroupByBucket], self._query.get())

    def count(self) -> int:
        """Return the number of groups."""
        return self._query.count()
