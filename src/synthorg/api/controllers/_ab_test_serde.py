"""A/B-test record serialisation for the meta read endpoints.

Extracted from ``meta`` so the controller stays within its size budget
while the serialiser remains a pure, reusable transform over a durable
``AbTestRecord``.
"""

from synthorg.meta.rollout.ab_models import AbTestRecord


def ab_test_to_dict(record: AbTestRecord) -> dict[str, object]:
    """Serialise a durable A/B-test record for the read endpoints.

    Returns:
        A JSON-serialisable summary dict.
    """
    return {
        "id": str(record.id),
        "name": str(record.name),
        "status": record.status.value,
        "verdict": record.verdict.value if record.verdict is not None else None,
        "observation_hours_elapsed": record.observation_hours_elapsed,
        "arms": [
            {
                "name": str(arm.name),
                "agent_count": arm.agent_count,
                "fraction": arm.fraction,
            }
            for arm in record.arms
        ],
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }
