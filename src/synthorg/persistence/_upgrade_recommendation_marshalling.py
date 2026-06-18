"""Row marshalling for upgrade-recommendation repositories.

Shared by both backend repositories so the row -> entity decode is
identical on SQLite and Postgres (one error path, one test surface).
The ``recommendation`` and ``agent_ids`` columns are JSON text; the
status/timestamp columns are scalar so the review surface can filter.
"""

import json
from uuid import UUID

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.upgrade_recommendation import (
    PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence._shared.rows import RowLike
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.management.upgrade_models import (
    StoredUpgradeRecommendation,
    UpgradeRecommendation,
)

logger = get_logger(__name__)


def row_to_recommendation(row: RowLike) -> StoredUpgradeRecommendation:
    """Convert a database row into a :class:`StoredUpgradeRecommendation`.

    Returns:
        The decoded entity.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        decided_at_raw = row["decided_at"]
        decided_by_raw = row["decided_by"]
        agent_ids = tuple(json.loads(str(row["agent_ids_json"])))
        return StoredUpgradeRecommendation(
            id=UUID(str(row["id"])),
            recommendation=UpgradeRecommendation.model_validate_json(
                str(row["recommendation_json"]),
            ),
            agent_ids=agent_ids,
            status=RecommendationStatus(str(row["status"])),
            created_at=coerce_row_timestamp(row["created_at"]),
            decided_at=(
                coerce_row_timestamp(decided_at_raw)
                if decided_at_raw is not None
                else None
            ),
            decided_by=str(decided_by_raw) if decided_by_raw is not None else None,
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        msg = "Corrupt upgrade_recommendation row"
        logger.warning(
            PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
            operation="row_to_recommendation",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc
