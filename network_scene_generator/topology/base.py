from __future__ import annotations

from pathlib import Path
from typing import Callable

import networkx as nx


class TopologyParseError(RuntimeError):
    """Raised when a topology file cannot be parsed into a graph."""


ParserFunc = Callable[[Path], nx.Graph]
