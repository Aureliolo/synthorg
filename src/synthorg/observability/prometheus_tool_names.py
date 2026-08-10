"""The ``tool_name`` label allowlist, published by the tool registries.

Every other bounded label in :mod:`synthorg.observability.prometheus_labels` is
pulled on a scrape from a snapshot of what the process is configured with. This
one cannot be: the registries that decide the valid set are built per task,
long after boot, in a process that may have no scraper attached at all, so a
scrape-time pull is neither timely nor guaranteed to happen. Pulling it from
``AppState`` (which has no tool registry, and never had one) left it
permanently empty: every tool invocation was rejected, no per-tool metric was
ever recorded, and a live run logged two warnings per tool call for its whole
duration.

So the flow is inverted here, and the state is a small module-level registry a
``ToolRegistry`` writes into as it is constructed. That is enough of its own
concern, with its own lock, ceiling and latch, to live beside the snapshot
rather than inside it.
"""

import threading
from collections.abc import Iterable
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.metrics import METRICS_TOOL_LABEL_CAP_REACHED
from synthorg.observability.prometheus_labels import require_label_summary

logger = get_logger(__name__)

_agent_tool_names: frozenset[str] = frozenset()

# The publisher is synchronous and can run on any thread, so it cannot take
# the async snapshot lock; its own lock keeps the read-modify-write from
# losing a concurrently-registered name.
_agent_tool_names_lock: Final[threading.Lock] = threading.Lock()

# Ceiling on the admitted set. "The tools this process can construct" bounds
# the local ones, but an MCP bridge names its tools from a remote server's
# ``tools/list``, so a server that answers with fresh names on every reconnect
# chooses how large this grows: one permanent Prometheus series per name, plus
# the resident set, neither of which anything reclaims. Far above the ~250
# built-in tools plus any plausible MCP fleet, so reaching it means something
# is minting names rather than that a deployment got big.
_MAX_AGENT_TOOL_NAMES: Final[int] = 5000

# Latched once the ceiling is hit, so the warning is logged on the transition
# rather than on every registry built from then on.
_agent_tool_names_capped: bool = False


def register_agent_tool_names(names: Iterable[str]) -> None:
    """Admit *names* as valid ``tool_name`` label values.

    The union grows monotonically, bounded by the tools this process can
    actually construct: a tool that was never built cannot be invoked, so it
    can never be labelled. That bound is local to this process but not to this
    deployment, because an MCP bridge takes its names from a remote server, so
    the set is additionally capped. A batch that would cross the cap is not
    refused whole: the names that fit are admitted and only the overflow is
    turned away, which drops those names' per-tool metric and leaves every
    invocation untouched. Which names fit is decided by sort order rather than
    by iteration order, so a registry rebuilt from the same tools admits the
    same ones instead of a different arbitrary slice each time.

    Args:
        names: Tool names a freshly-built registry exposes.
    """
    global _agent_tool_names, _agent_tool_names_capped  # noqa: PLW0603
    incoming = frozenset(names)
    with _agent_tool_names_lock:
        fresh = incoming - _agent_tool_names
        # Never negative: the set is only ever grown to the cap, so an
        # already-admitted batch takes the fitting branch with nothing to add.
        room = _MAX_AGENT_TOOL_NAMES - len(_agent_tool_names)
        if len(fresh) <= room:
            _agent_tool_names = _agent_tool_names | fresh
            return
        _agent_tool_names = _agent_tool_names | frozenset(sorted(fresh)[:room])
        if not _agent_tool_names_capped:
            _agent_tool_names_capped = True
            logger.warning(
                METRICS_TOOL_LABEL_CAP_REACHED,
                admitted=len(_agent_tool_names),
                refused=len(fresh) - room,
                cap=_MAX_AGENT_TOOL_NAMES,
            )


def _reset_agent_tool_names_for_tests() -> None:
    """Reset the agent tool-name allowlist to bootstrap. Test-only."""
    global _agent_tool_names, _agent_tool_names_capped  # noqa: PLW0603
    with _agent_tool_names_lock:
        _agent_tool_names = frozenset()
        _agent_tool_names_capped = False


def validate_tool_name(value: str) -> None:
    """Raise ``ValueError`` if *value* is not a registered tool name.

    Bounds the ``tool_name`` Prometheus label against the tool registries
    this process has built, so plugin- and MCP-loaded tools are accepted but
    a runaway caller that fabricates names cannot inflate cardinality. Fails
    closed before the first registry is built; push-time callers go through
    ``metrics_hub._safe_record`` so the rejected sample drops cleanly.
    """
    require_label_summary("tool_name", value, _agent_tool_names)


__all__ = ["register_agent_tool_names", "validate_tool_name"]
