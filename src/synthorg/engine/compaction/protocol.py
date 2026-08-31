"""Compaction callback protocol.

A ``Protocol`` rather than a bare ``Callable`` alias, so a forced,
agent-directed call -- ``force=True`` skipping the fill-threshold check,
``preserve_markers`` overriding the config default for exactly this call --
is part of the shape every implementation and every caller agrees on.
"""

from typing import Protocol

from synthorg.engine.context import AgentContext


class CompactionCallback(Protocol):
    """Async callback invoked at turn boundaries to compress conversation.

    Receives the current ``AgentContext`` and returns either a new
    ``AgentContext`` with compressed conversation, or ``None`` when no
    compaction ran (below threshold, too few messages, or nothing left to
    archive).
    """

    async def __call__(
        self,
        ctx: AgentContext,
        *,
        force: bool = False,
        preserve_markers: bool | None = None,
    ) -> AgentContext | None:
        """Compress *ctx*'s conversation, or decline.

        Args:
            ctx: Current agent context.
            force: Skip the fill-threshold check; set for an agent-directed
                request via the ``compact_context`` tool, never for the
                periodic turn-boundary call.
            preserve_markers: Per-call override for
                ``CompactionConfig.preserve_epistemic_markers``; ``None``
                uses the configured default.

        Returns:
            The compacted context, or ``None`` when compaction did not run.
        """
        ...
