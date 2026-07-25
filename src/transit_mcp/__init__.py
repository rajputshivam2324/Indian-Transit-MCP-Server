"""Indian Multi-Modal Transit MCP server.

The package is split into a reusable, framework-agnostic core (``models``,
``interfaces``, ``services``, ``providers``, ``infra``) and a thin MCP adapter
(``server``). Agents may import the core directly without touching MCP.
"""

from __future__ import annotations

__version__ = "0.1.0"
