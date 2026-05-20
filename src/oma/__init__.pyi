"""Type stub for oma — keeps mypy happy with PEP 562 lazy loading."""

from oma import adapters as adapters
from oma import bank as bank
from oma import capture as capture
from oma import core as core
from oma import distill as distill
from oma import forum as forum
from oma import utils as utils
from oma import web as web
from oma.adapters import Adapter as Adapter
from oma.capture import CanonicalTrace as CanonicalTrace
from oma.capture import ScrubReport as ScrubReport
from oma.capture import TraceEvent as TraceEvent
from oma.core import Agent as Agent
from oma.core import Collection as Collection
from oma.core import Goal as Goal
from oma.core import KnowledgePacket as KnowledgePacket
from oma.core import Packet as Packet
from oma.core import Session as Session

__version__: str

def setup_environment() -> None: ...
