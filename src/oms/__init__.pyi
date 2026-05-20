"""Type stub for oms — keeps mypy happy with PEP 562 lazy loading."""

from oms import adapters as adapters
from oms import bank as bank
from oms import capture as capture
from oms import core as core
from oms import distill as distill
from oms import forum as forum
from oms import utils as utils
from oms import web as web
from oms.adapters import Adapter as Adapter
from oms.capture import CanonicalTrace as CanonicalTrace
from oms.capture import ScrubReport as ScrubReport
from oms.capture import TraceEvent as TraceEvent
from oms.core import Agent as Agent
from oms.core import Collection as Collection
from oms.core import Goal as Goal
from oms.core import KnowledgePacket as KnowledgePacket
from oms.core import Packet as Packet
from oms.core import Session as Session

__version__: str

def setup_environment() -> None: ...
