import logging
import time
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from typing import TYPE_CHECKING
from typing import Any
from typing import Final
from typing import Literal
from typing import NotRequired
from typing import Self
from typing import TypedDict
from typing import cast
from typing import overload
from typing import override
from urllib.parse import quote_plus
from urllib.parse import urlunparse

import httpx2

try:
    __version__ = version("pyalex")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

logger = logging.getLogger("pyalex")


class AlexConfig(dict[str, Any]):
    """Configuration class for OpenAlex API.

    Attributes
    ----------
    email : str
        Email address for API requests.
    api_key : str
        API key for authentication.
    user_agent : str
        User agent string for API requests.
    openalex_url : str
        Base URL for OpenAlex API.
    timeout : float
        Timeout in seconds for a single HTTP request.
    max_retries : int
        Maximum number of retries for API requests.
    retry_backoff_factor : float
        Backoff factor for retries.
    retry_http_codes : list
        List of HTTP status codes to retry on.
    """

    # Attributes with type annotations for static analysis and autocompletion
    email: str | None
    api_key: str | None
    user_agent: str
    openalex_url: str
    timeout: float
    max_retries: int
    retry_backoff_factor: float
    retry_http_codes: list[int]

    # Define all attributes in __init__ to ensure that
    # type checkers can and will only recognize them.
    # This provides static checking support for potential typos by users
    # For example, type checker will not allow `config.emaila`
    # but `config.emaila` can still run and be accessed
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

    # Enable dynamic property capture only at runtime
    # Type checkers only recognize properties explicitly declared above
    # but at runtime, dynamic dict attributes can still be accessed.
    if not TYPE_CHECKING:

        def __getattr__(self, key: str) -> Any:
            try:
                return super().__getitem__(key)
            except KeyError:
                raise AttributeError(key) from None

        def __setattr__(self, key: str, value: Any) -> None:
            super().__setitem__(key, value)


config = AlexConfig(
    email=None,
    api_key=None,
)


class or_(dict[str, Any]):
    """Logical OR expression class."""


class _LogicalExpression:
    """Base class for logical expressions.

    Attributes
    ----------
    token : str
        Token representing the logical operation.
    value : any
        Value to be used in the logical expression.
    """

    token: str | None = None

    def __init__(self, value: Any) -> None:
        self.value = value

    @override
    def __str__(self) -> str:
        return f"{self.token}{self.value}"


class not_(_LogicalExpression):
    """Logical NOT expression class."""

    token = "!"


class gt_(_LogicalExpression):
    """Logical greater than expression class."""

    token = ">"


class lt_(_LogicalExpression):
    """Logical less than expression class."""

    token = "<"


def _quote_oa_value(v: Any) -> Any:
    """Prepare a value for the OpenAlex API.

    Applies URL encoding to strings and converts booleans to lowercase strings.

    Parameters
    ----------
    v : any
        Value to be prepared.

    Returns
    -------
    any
        Prepared value.
    """

    # workaround for bug https://groups.google.com/u/1/g/openalex-users/c/t46RWnzZaXc
    if isinstance(v, bool):
        return str(v).lower()

    if isinstance(v, _LogicalExpression) and isinstance(v.value, str):
        # Return a new instance instead of mutating ``v`` in place: repeated
        # calls (e.g. every access to ``.url``) must not re-encode the value.
        return type(v)(quote_plus(v.value))

    if isinstance(v, str):
        return quote_plus(v)

    return v


def _flatten_kv(
    d: Any, prefix: str | None = None, logical: Literal["+", "|"] = "+"
) -> str:
    """Flatten a dictionary into a key-value string for the OpenAlex API.

    Parameters
    ----------
    d : dict
        Dictionary to be flattened.
    prefix : str, optional
        Prefix for the keys.
    logical : Literal["+", "|"] , optional
        Logical operator to join values.

    Returns
    -------
    str
        Flattened key-value string.
    """
    if prefix is None and not isinstance(d, dict):
        raise ValueError("prefix should be set if d is not a dict")

    if isinstance(d, dict):
        logical_subd = "|" if isinstance(d, or_) else logical

        t = []
        for k, v in d.items():
            x = _flatten_kv(
                v, prefix=f"{prefix}.{k}" if prefix else f"{k}", logical=logical_subd
            )
            t.append(x)

        return ",".join(t)
    elif isinstance(d, list):
        list_str = logical.join([f"{_quote_oa_value(i)}" for i in d])
        return f"{prefix}:{list_str}"
    else:
        return f"{prefix}:{_quote_oa_value(d)}"


def _params_merge(params: dict[str, Any], add_params: dict[str, Any]) -> None:
    """Merge additional parameters into existing parameters.

    Parameters
    ----------
    params : dict
        Existing parameters.
    add_params : dict
        Additional parameters to be merged.
    """
    for k in add_params:
        if (
            k in params
            and isinstance(params[k], dict)
            and isinstance(add_params[k], dict)
        ):
            _params_merge(params[k], add_params[k])
        elif (
            k in params
            and not isinstance(params[k], list)
            and isinstance(add_params[k], list)
        ):
            # example: params="a" and add_params=["b", "c"]
            params[k] = [params[k]] + add_params[k]
        elif (
            k in params
            and isinstance(params[k], list)
            and not isinstance(add_params[k], list)
        ):
            # example: params=["b", "c"] and add_params="a"
            params[k] = params[k] + [add_params[k]]
        elif k in params:
            params[k] = [params[k], add_params[k]]
        else:
            params[k] = add_params[k]


_client: httpx2.Client | None = None


def _get_client() -> httpx2.Client:
    """Return the shared HTTP client.

    A single long-lived client keeps the connection pool warm, so consecutive
    requests reuse already established TLS connections instead of handshaking
    again.

    Returns
    -------
    httpx2.Client
        Shared client with the configured timeout and connection limits.
    """
    global _client
    if _client is None:
        _client = httpx2.Client(
            timeout=config.timeout,
            follow_redirects=True,
            limits=httpx2.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
        )

    return _client


def _send(client: httpx2.Client, url: str, *, auth: httpx2.Auth) -> httpx2.Response:
    """Send a GET request, retrying transport errors and configured status codes.

    Parameters
    ----------
    client : httpx2.Client
        Client to use, normally the shared one.
    url : str
        URL to request.
    auth : httpx2.Auth
        Authentication flow adding the OpenAlex headers.

    Returns
    -------
    httpx2.Response
        The first response that is not retried.
    """
    attempts = max(1, config.max_retries + 1)

    for attempt in range(attempts):
        is_last_attempt = attempt + 1 >= attempts
        try:
            response = client.get(url, auth=auth, timeout=config.timeout)
        except httpx2.TransportError:
            if is_last_attempt:
                raise
            time.sleep(config.retry_backoff_factor * (2**attempt))
            continue

        if response.status_code in config.retry_http_codes and not is_last_attempt:
            time.sleep(config.retry_backoff_factor * (2**attempt))
            continue

        return response

    raise RuntimeError("unreachable: retry loop always returns or raises")


def invert_abstract(inv_index: dict[str, list[int]] | None) -> str | None:
    """Invert OpenAlex abstract index.

    Parameters
    ----------
    inv_index : dict
        Inverted index of the abstract.

    Returns
    -------
    str
        Inverted abstract.
    """
    if inv_index is not None:
        l_inv = [(w, p) for w, pos in inv_index.items() for p in pos]
        return " ".join(x[0] for x in sorted(l_inv, key=lambda x: x[1]))


def _wrap_values_nested_dict(
    d: dict[str, Any], func: Callable[[Any], Any]
) -> dict[str, Any]:
    """Apply a function to all values in a nested dictionary.

    Parameters
    ----------
    d : dict
        Nested dictionary.
    func : function
        Function to apply to the values.

    Returns
    -------
    dict
        Dictionary with the function applied to the values.
    """
    for k, v in d.items():
        if isinstance(v, dict):
            d[k] = _wrap_values_nested_dict(v, func)
        elif isinstance(v, list):
            d[k] = [func(i) for i in v]
        else:
            d[k] = func(v)

    return d


class QueryError(ValueError):
    """Exception raised for errors in the query."""


class OpenAlexEntity(dict[str, Any]):
    """Base class for OpenAlex entities."""


class Meta(TypedDict):
    """Metadata returned alongside a list of OpenAlex entities.

    All keys are optional because the exact set depends on the endpoint and
    on the query parameters (e.g. ``page`` is absent when cursor paging is used).
    """

    count: int
    db_response_time_ms: int
    page: NotRequired[int]
    per_page: NotRequired[int]
    next_cursor: NotRequired[str | None]
    groups_count: NotRequired[int]


class OpenAlexResponseList[T: OpenAlexEntity](list[T]):
    """A list of OpenAlexEntity objects with metadata.

    Attributes:
        meta: a dictionary with metadata about the results
        resource_class: the class to use for each entity in the results

    Arguments:
        results: a list of OpenAlexEntity objects
        meta: a dictionary with metadata about the results
        resource_class: the class to use for each entity in the results

    Returns:
        a OpenAlexResponseList object
    """

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
    """One bucket of a ``group_by`` response.

    OpenAlex returns ``{"key": ..., "key_display_name": ..., "count": ...}``
    for every group. These rows are not entities, so they get their own type.
    """

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
            if len(r) > 0:
                assert r.meta is not None
                self._next_value = r.meta["page"] + 1
            else:
                self._next_value = None

        self.n = self.n + len(r)

        return r


class OpenAlexAuth(httpx2.Auth):
    """OpenAlex auth class based on httpx auth.

    Includes the email, api_key, and user-agent headers.

    Parameters
    ----------
    config : AlexConfig
        Configuration object for OpenAlex API.
    """

    def __init__(self, config: AlexConfig) -> None:
        self.config = config

    @override
    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response, None]:
        if self.config.api_key:
            request.headers["Authorization"] = f"Bearer {self.config.api_key}"

        if self.config.email:
            request.headers["From"] = self.config.email

        if self.config.user_agent:
            request.headers["User-Agent"] = self.config.user_agent

        yield request


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

    if params and "group-by" in params:
        # ``group_by`` rows are buckets, not entities of type ResponseT.
        return cast(
            OpenAlexResponseList[ResponseT],
            OpenAlexResponseList(res_json["group_by"], res_json["meta"], GroupByBucket),
        )
    elif "results" in res_json:
        return OpenAlexResponseList(
            res_json["results"], res_json["meta"], resource_class
        )
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


class BaseContent:
    """Class representing content in OpenAlex."""

    def __init__(self, key: str) -> None:
        self.key = key

    @override
    def __repr__(self) -> str:
        return f"Content(key='{self.key}')"

    @property
    def url(self) -> str:
        """Get the URL for the content.

        Returns
        -------
        str
            URL for the content.
        """
        return f"https://content.openalex.org/works/{self.key}"

    def get(self) -> bytes:
        """Get the content.

        Requests ``self.url`` so that subclasses fetch the format they
        advertise (``.pdf`` for :class:`PDF`, ``.grobid-xml`` for :class:`TEI`).

        Returns
        -------
        bytes
            Content of the request.
        """
        res = _send(_get_client(), self.url, auth=OpenAlexAuth(config))
        res.raise_for_status()
        return res.content

    def download(self, filepath: str) -> None:
        """Download the content to a file.

        Parameters
        ----------
        filepath : str
            Path to save the content.
        """

        with open(filepath, "wb") as f:
            f.write(self.get())


# The API


class PDF(BaseContent):
    """Class representing a PDF content in OpenAlex."""

    @property
    @override
    def url(self) -> str:
        """Get the URL for the content.

        Returns
        -------
        str
            URL for the content.
        """
        return f"https://content.openalex.org/works/{self.key}.pdf"


class TEI(BaseContent):
    """Class representing a TEI content in OpenAlex."""

    @property
    @override
    def url(self) -> str:
        """Get the URL for the content.

        Returns
        -------
        str
            URL for the content.
        """
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
        """Get the PDF content for the work.

        Returns
        -------
        PDF
            PDF content object.
        """
        return PDF(self["id"].split("/")[-1])

    @property
    def tei(self) -> TEI:
        """Get the TEI content for the work.

        Returns
        -------
        TEI
            TEI content object.
        """
        return TEI(self["id"].split("/")[-1])


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


class Author(OpenAlexEntity):
    """Class representing an author entity in OpenAlex."""


class Authors(BaseOpenAlex[Author]):
    """Class representing a collection of author entities in OpenAlex."""

    resource_class = Author


class Source(OpenAlexEntity):
    """Class representing a source entity in OpenAlex."""


class Sources(BaseOpenAlex[Source]):
    """Class representing a collection of source entities in OpenAlex."""

    resource_class = Source


class Institution(OpenAlexEntity):
    """Class representing an institution entity in OpenAlex."""


class Institutions(BaseOpenAlex[Institution]):
    """Class representing a collection of institution entities in OpenAlex."""

    resource_class = Institution


class Domain(OpenAlexEntity):
    """Class representing a domain entity in OpenAlex."""


class Domains(BaseOpenAlex[Domain]):
    """Class representing a collection of domain entities in OpenAlex."""

    resource_class = Domain


class Field(OpenAlexEntity):
    """Class representing a field entity in OpenAlex."""


class Fields(BaseOpenAlex[Field]):
    """Class representing a collection of field entities in OpenAlex."""

    resource_class = Field


class Subfield(OpenAlexEntity):
    """Class representing a subfield entity in OpenAlex."""


class Subfields(BaseOpenAlex[Subfield]):
    """Class representing a collection of subfield entities in OpenAlex."""

    resource_class = Subfield


class Topic(OpenAlexEntity):
    """Class representing a topic entity in OpenAlex."""


class Topics(BaseOpenAlex[Topic]):
    """Class representing a collection of topic entities in OpenAlex."""

    resource_class = Topic


class Publisher(OpenAlexEntity):
    """Class representing a publisher entity in OpenAlex."""


class Publishers(BaseOpenAlex[Publisher]):
    """Class representing a collection of publisher entities in OpenAlex."""

    resource_class = Publisher


class Funder(OpenAlexEntity):
    """Class representing a funder entity in OpenAlex."""


class Funders(BaseOpenAlex[Funder]):
    """Class representing a collection of funder entities in OpenAlex."""

    resource_class = Funder


class Award(OpenAlexEntity):
    """Class representing an award entity in OpenAlex."""


class Awards(BaseOpenAlex[Award]):
    """Class representing a collection of award entities in OpenAlex."""

    resource_class = Award


class Keyword(OpenAlexEntity):
    """Class representing a keyword entity in OpenAlex."""


class Keywords(BaseOpenAlex[Keyword]):
    """Class representing a collection of keyword entities in OpenAlex."""

    resource_class = Keyword


class Autocomplete(OpenAlexEntity):
    """Class representing an autocomplete entity in OpenAlex."""


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
