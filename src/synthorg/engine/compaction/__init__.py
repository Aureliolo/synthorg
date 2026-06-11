"""Context compaction subpackage.

Provides a pluggable compaction hook for execution loops that
compresses older conversation turns when the context window
fill level exceeds a configurable threshold.

The init re-exports only the leaf models and stays clear of
``compaction.protocol``: ``engine.context`` imports ``compaction.models``
(running this init), and ``compaction.protocol`` names
``engine.context.AgentContext`` at runtime so its ``CompactionCallback``
alias resolves under typeguard. Pulling ``compaction.protocol`` through this
init would close that cold-import cycle, so import the callback alias from
its defining submodule:
``from synthorg.engine.compaction.protocol import CompactionCallback``.
"""

from synthorg.engine.compaction.models import (
    CompactionConfig,
    CompressionMetadata,
)

__all__ = [
    "CompactionConfig",
    "CompressionMetadata",
]
