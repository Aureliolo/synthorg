"""Custom signal rule service layer.

Wraps :class:`CustomRuleRepository` so the
``/meta/custom-rules`` controller stays thin and all
``META_CUSTOM_RULE_*`` audit logging lives in one place.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.meta.rules.custom import CustomRuleDefinition
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_CUSTOM_RULE_CREATED,
    META_CUSTOM_RULE_DELETE_FAILED,
    META_CUSTOM_RULE_DELETED,
    META_CUSTOM_RULE_FETCH_FAILED,
    META_CUSTOM_RULE_TOGGLED,
    META_CUSTOM_RULE_UPDATE_REJECTED,
    META_CUSTOM_RULE_UPDATED,
)

# Fields that are managed by the persistence layer / immutable after
# creation. Callers must not override these via partial updates; doing so
# would turn an update into an identity change or rewrite audit history.
_IMMUTABLE_RULE_FIELDS: frozenset[str] = frozenset({"id", "created_at"})

if TYPE_CHECKING:
    from synthorg.persistence.custom_rule_repo import CustomRuleRepository

logger = get_logger(__name__)


class CustomRuleNotFoundError(NotFoundError):
    """Raised when an id-targeted update/toggle/delete misses.

    Inherits :class:`NotFoundError` so the central exception
    handler maps the exception directly to a 404 RFC 9457 response;
    controllers no longer translate to the generic
    :class:`NotFoundError` themselves.
    """


class CustomRulesService:
    """CRUD + toggle orchestration with uniform audit logging."""

    __slots__ = ("_repo",)

    def __init__(self, *, repo: CustomRuleRepository) -> None:
        self._repo = repo

    async def list_rules(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[CustomRuleDefinition, ...], int]:
        """Return paginated rules plus the unfiltered total.

        Args:
            offset: Non-negative page offset.
            limit: Optional positive page size; ``None`` returns every
                rule from ``offset`` onwards.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is
                provided and non-positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        all_rules = await self._repo.list_rules()
        total = len(all_rules)
        end = total if limit is None else offset + limit
        return tuple(all_rules[offset:end]), total

    async def get(self, rule_id: NotBlankStr) -> CustomRuleDefinition | None:
        """Return a single rule by id, or ``None`` when missing."""
        return await self._repo.get(rule_id)

    async def create(self, definition: CustomRuleDefinition) -> CustomRuleDefinition:
        """Persist a new rule and emit an audit log."""
        await self._repo.save(definition)
        logger.info(
            META_CUSTOM_RULE_CREATED,
            rule_id=str(definition.id),
            rule_name=definition.name,
        )
        return definition

    async def update(
        self,
        rule_id: NotBlankStr,
        updates: dict[str, object],
    ) -> CustomRuleDefinition:
        """Apply a partial update to the rule with *rule_id*.

        Rejects overrides of immutable fields (``id``, ``created_at``)
        so callers cannot accidentally rewrite identity or audit
        history by including those keys in ``updates``. The exact set
        lives in :data:`_IMMUTABLE_RULE_FIELDS`; it intentionally tracks
        only the fields that actually exist on
        :class:`CustomRuleDefinition` (the model does not carry a
        ``created_by`` attribute).

        Raises:
            CustomRuleNotFoundError: If the target id does not exist.
            ValueError: If *updates* tries to set an immutable field.
        """
        forbidden = _IMMUTABLE_RULE_FIELDS.intersection(updates)
        if forbidden:
            logger.warning(
                META_CUSTOM_RULE_UPDATE_REJECTED,
                rule_id=str(rule_id),
                forbidden_fields=sorted(forbidden),
                reason="immutable_field_override",
            )
            msg = f"Immutable custom-rule fields cannot be updated: {sorted(forbidden)}"
            raise ValueError(msg)
        existing = await self._repo.get(rule_id)
        if existing is None:
            logger.warning(
                META_CUSTOM_RULE_FETCH_FAILED,
                rule_id=str(rule_id),
                operation="update",
                reason="not_found",
            )
            msg = f"Custom rule {rule_id} not found"
            raise CustomRuleNotFoundError(msg)
        payload = {
            **existing.model_dump(),
            **updates,
            "updated_at": datetime.now(UTC),
        }
        # Re-seat the immutable fields from ``existing`` so an attacker
        # who slipped past the forbidden-key filter (via casing, etc.)
        # still cannot override them.
        payload["id"] = existing.id
        payload["created_at"] = existing.created_at
        updated = CustomRuleDefinition.model_validate(payload)
        await self._repo.save(updated)
        logger.info(
            META_CUSTOM_RULE_UPDATED,
            rule_id=str(rule_id),
            rule_name=updated.name,
        )
        return updated

    async def delete(self, rule_id: NotBlankStr) -> None:
        """Delete a rule by id.

        Raises:
            CustomRuleNotFoundError: If no rule with *rule_id* exists.
        """
        deleted = await self._repo.delete(rule_id)
        if not deleted:
            logger.warning(
                META_CUSTOM_RULE_DELETE_FAILED,
                rule_id=str(rule_id),
                operation="delete",
                reason="not_found",
            )
            msg = f"Custom rule {rule_id} not found"
            raise CustomRuleNotFoundError(msg)
        logger.info(META_CUSTOM_RULE_DELETED, rule_id=str(rule_id))

    async def toggle(self, rule_id: NotBlankStr) -> CustomRuleDefinition:
        """Flip the ``enabled`` flag on the rule with *rule_id*.

        Raises:
            CustomRuleNotFoundError: If no rule with *rule_id* exists.
        """
        existing = await self._repo.get(rule_id)
        if existing is None:
            logger.warning(
                META_CUSTOM_RULE_FETCH_FAILED,
                rule_id=str(rule_id),
                operation="toggle",
                reason="not_found",
            )
            msg = f"Custom rule {rule_id} not found"
            raise CustomRuleNotFoundError(msg)
        toggled = existing.model_copy(
            update={
                "enabled": not existing.enabled,
                "updated_at": datetime.now(UTC),
            },
        )
        await self._repo.save(toggled)
        logger.info(
            META_CUSTOM_RULE_TOGGLED,
            rule_id=str(rule_id),
            enabled=toggled.enabled,
        )
        return toggled
