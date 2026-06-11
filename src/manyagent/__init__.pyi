"""Type stub for manyagent — keeps mypy happy with PEP 562 lazy loading."""

from manyagent import adapters as adapters
from manyagent import bank as bank
from manyagent import capture as capture
from manyagent import core as core
from manyagent import distill as distill
from manyagent import forum as forum
from manyagent import testing as testing
from manyagent import utils as utils
from manyagent import web as web
from manyagent.adapters import Adapter as Adapter
from manyagent.capture import CanonicalTrace as CanonicalTrace
from manyagent.capture import ScrubReport as ScrubReport
from manyagent.capture import TraceEvent as TraceEvent
from manyagent.core import Agent as Agent
from manyagent.core import Collection as Collection
from manyagent.core import Goal as Goal
from manyagent.core import KnowledgePacket as KnowledgePacket
from manyagent.core import Packet as Packet
from manyagent.core import Session as Session

__version__: str

def setup_environment() -> None: ...
