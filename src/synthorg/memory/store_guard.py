"""Store-boundary tag enforcement for non-inferable principle.

Advisory guard that warns when memories are stored without the
``"non-inferable"`` tag.  Never blocks — the store always succeeds.
"""

from typing import TYPE_CHECKING

from synthorg.memory.filter import NON_INFERABLE_TAG
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FILTER_STORE_MISSING_TAG,
)

if TYPE_CHECKING:
    from synthorg.memory.models import MemoryStoreRequest

logger = get_logger(__name__)


def validate_memory_tags(request: MemoryStoreRequest) -> None:
    """Log a warning when the non-inferable tag is missing.

    This is advisory only — the store operation is never blocked.
    Wire into ``MemoryBackend.store()`` callers to activate enforcement.

    Args:
        request: The memory store request to validate.
    """
    if NON_INFERABLE_TAG not in request.metadata.tags:
        logger.warning(
            MEMORY_FILTER_STORE_MISSING_TAG,
            category=request.category.value,
            content_length=len(request.content),
        )
