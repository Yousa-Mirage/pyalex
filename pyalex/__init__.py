from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("pyalex")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__version_tuple__: tuple[int, ...] = tuple(
    int(part) for part in __version__.split(".")[:3] if part.isdigit()
)

from pyalex.api import Author
from pyalex.api import Authors
from pyalex.api import Award
from pyalex.api import Awards
from pyalex.api import CreditsExhaustedError
from pyalex.api import Domain
from pyalex.api import Domains
from pyalex.api import Field
from pyalex.api import Fields
from pyalex.api import Funder
from pyalex.api import Funders
from pyalex.api import GroupByBucket
from pyalex.api import GroupByQuery
from pyalex.api import Institution
from pyalex.api import Institutions
from pyalex.api import Journals
from pyalex.api import Keyword
from pyalex.api import Keywords
from pyalex.api import OpenAlexResponseList
from pyalex.api import People
from pyalex.api import Publisher
from pyalex.api import Publishers
from pyalex.api import RateLimit
from pyalex.api import Source
from pyalex.api import Sources
from pyalex.api import Subfield
from pyalex.api import Subfields
from pyalex.api import Topic
from pyalex.api import Topics
from pyalex.api import Work
from pyalex.api import Works
from pyalex.api import autocomplete
from pyalex.api import config
from pyalex.api import invert_abstract

__all__ = [
    "Author",
    "Authors",
    "Award",
    "Awards",
    "CreditsExhaustedError",
    "Domain",
    "Domains",
    "Field",
    "Fields",
    "Funder",
    "Funders",
    "GroupByBucket",
    "GroupByQuery",
    "Institution",
    "Institutions",
    "Journals",
    "Keyword",
    "Keywords",
    "OpenAlexResponseList",
    "People",
    "Publisher",
    "Publishers",
    "RateLimit",
    "Source",
    "Sources",
    "Subfield",
    "Subfields",
    "Topic",
    "Topics",
    "Work",
    "Works",
    "autocomplete",
    "config",
    "invert_abstract",
]
