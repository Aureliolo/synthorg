"""WebSocket channel constants and plugin factory.

Defines the named channels for real-time event feeds and
creates the Litestar ``ChannelsPlugin`` with an in-memory backend.
"""

from litestar.channels import ChannelsPlugin
from litestar.channels.backends.memory import MemoryChannelsBackend

CHANNEL_TASKS: str = "tasks"
CHANNEL_AGENTS: str = "agents"
CHANNEL_BUDGET: str = "budget"
CHANNEL_MESSAGES: str = "messages"
CHANNEL_SYSTEM: str = "system"
CHANNEL_APPROVALS: str = "approvals"

ALL_CHANNELS: tuple[str, ...] = (
    CHANNEL_TASKS,
    CHANNEL_AGENTS,
    CHANNEL_BUDGET,
    CHANNEL_MESSAGES,
    CHANNEL_SYSTEM,
    CHANNEL_APPROVALS,
)


def create_channels_plugin() -> ChannelsPlugin:
    """Create the channels plugin with in-memory backend.

    Returns:
        Configured ``ChannelsPlugin`` with 20-message history
        per channel and no arbitrary channel creation.
    """
    return ChannelsPlugin(
        backend=MemoryChannelsBackend(history=20),
        channels=ALL_CHANNELS,
        arbitrary_channels_allowed=False,
    )
