"""Action type taxonomy -- categories, registry, and validation.

Provides ``ActionTypeCategory`` for the 11 top-level categories and
``ActionTypeRegistry`` for validating built-in and custom action types,
expanding category shortcuts, and querying the taxonomy.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_ACTION_TYPE_INVALID,
    SECURITY_CONFIG_LOADED,
)
from synthorg.security.autonomy.enums import ActionType

logger = get_logger(__name__)


class ActionTypeCategory(StrEnum):
    """Top-level action type category prefixes."""

    CODE = "code"
    TEST = "test"
    DOCS = "docs"
    DESIGN = "design"
    VCS = "vcs"
    DEPLOY = "deploy"
    PUBLISH = "publish"
    COMMS = "comms"
    BUDGET = "budget"
    ORG = "org"
    DB = "db"
    ARCH = "arch"
    PLAN = "plan"
    TOOL = "tool"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    BROWSER = "browser"
    EXTERNAL_DATA = "external_data"
    DESKTOP = "desktop"
    RESEARCH = "research"


def _build_category_map() -> dict[str, frozenset[str]]:
    """Group all built-in ActionType members by category prefix.

    Returns:
        A mapping of category prefix to the frozenset of action-type
        values sharing it.
    """
    groups: dict[str, set[str]] = {}
    for member in ActionType:
        category = member.value.split(":")[0]
        groups.setdefault(category, set()).add(member.value)
    return {k: frozenset(v) for k, v in groups.items()}


_BUILTIN_TYPES: Final[frozenset[str]] = frozenset(member.value for member in ActionType)
_CATEGORY_MAP: Final[MappingProxyType[str, frozenset[str]]] = MappingProxyType(
    _build_category_map()
)

# Verify that every category extracted from ActionType has a matching
# ActionTypeCategory member, and vice versa.  This module-level check
# prevents silent drift between the enum and the category map.
_extracted_categories = frozenset(_CATEGORY_MAP.keys())
_enum_categories = frozenset(member.value for member in ActionTypeCategory)
_missing_in_enum = _extracted_categories - _enum_categories
_missing_in_map = _enum_categories - _extracted_categories
if _missing_in_enum:
    msg = f"ActionType categories missing from ActionTypeCategory: {_missing_in_enum}"
    raise RuntimeError(msg)
if _missing_in_map:
    msg = f"ActionTypeCategory members missing from ActionType: {_missing_in_map}"
    raise RuntimeError(msg)


#: Action types whose whole effect lands inside the agent's own throwaway
#: worktree, which is what makes auto-approving them under SUPERVISED safe:
#: the worktree is discarded and the review gate judges what comes out of it.
#: Membership is a claim about blast radius, not about the verb, so
#: ``code:delete`` belongs here (it deletes a file in a directory nobody
#: keeps) while ``design:delete`` does not (the asset store outlives the run).
#:
#: Declared rather than derived because nothing in the taxonomy records where
#: an action lands. A preset grants bare categories, so a new ``code:*``
#: member would otherwise join the auto-approved set with no decision;
#: ``check_autonomy_auto_approve_confined.py`` refuses that by requiring the
#: claim to be made here first.
#:
#: ``plan:extend_workstream`` is deliberately absent: grafting a slice
#: mutates the persisted ``Plan`` that drives the rest of the initiative,
#: which outlives every worktree, rather than something a discarded
#: worktree and a review gate can undo.
WORKTREE_CONFINED_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        ActionType.CODE_READ,
        ActionType.CODE_WRITE,
        ActionType.CODE_CREATE,
        ActionType.CODE_DELETE,
        ActionType.CODE_REFACTOR,
        ActionType.TEST_WRITE,
        ActionType.TEST_RUN,
        # Renders markup into the run's own output, and the living-doc
        # corpus it can write is fenced with TAG_LIVING_DOC when another
        # agent reads it back.
        ActionType.DOCS_WRITE,
        ActionType.VCS_READ,
        ActionType.VCS_COMMIT,
        ActionType.VCS_BRANCH,
        # Reads only, and the sandbox decides which database it can reach.
        ActionType.DB_QUERY,
        ActionType.COMMS_INTERNAL,
    }
)

#: Action types whose whole effect is a READ: they change nothing a later
#: call could observe, so the calls of one turn that carry them may run side
#: by side. Every other call runs one at a time in the order the model
#: issued it, which is the order it meant: a live run had two edits of one
#: file in one turn race each other, and the second hunk was applied to text
#: the first had already replaced. Declared rather than derived from the
#: verb because ``test:run`` reads like a read and leaves build artefacts
#: behind, and a custom type registered later must earn its way in rather
#: than be admitted by its spelling.
READ_ONLY_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        ActionType.CODE_READ,
        ActionType.VCS_READ,
        ActionType.DB_QUERY,
        ActionType.MEMORY_READ,
    }
)


class ActionTypeRegistry:
    """Validates built-in and custom action types.

    Supports category expansion (e.g. ``"code"`` → all ``code:*`` types)
    and registration of custom action types at runtime.

    Access the full set of registered types via ``all_types()``.
    """

    def __init__(
        self,
        *,
        custom_types: frozenset[str] = frozenset(),
    ) -> None:
        """Initialize with optional custom types.

        Args:
            custom_types: Additional action type strings to register.

        Raises:
            ValueError: If any custom type lacks a ``category:action`` format.
        """
        for ct in custom_types:
            if ct.count(":") != 1 or ct.startswith(":") or ct.endswith(":"):
                msg = (
                    f"Custom action type {ct!r} must use "
                    "'category:action' format (exactly one ':')"
                )
                logger.warning(SECURITY_ACTION_TYPE_INVALID, error=msg)
                raise ValueError(msg)
        self._custom_types = custom_types
        self._all_types = _BUILTIN_TYPES | custom_types
        logger.debug(
            SECURITY_CONFIG_LOADED,
            builtin_count=len(_BUILTIN_TYPES),
            custom_count=len(custom_types),
        )

    def is_registered(self, action_type: str) -> bool:
        """Check if an action type is known (built-in or custom).

        Returns:
            ``True`` if the action type is registered, ``False`` otherwise.
        """
        return action_type in self._all_types

    def validate(self, action_type: str) -> None:
        """Validate that an action type is registered.

        Args:
            action_type: The action type string to check.

        Raises:
            ValueError: If the action type is not registered.
        """
        if not self.is_registered(action_type):
            msg = f"Unknown action type: {action_type!r}"
            logger.warning(SECURITY_ACTION_TYPE_INVALID, error=msg)
            raise ValueError(msg)

    def expand_category(
        self,
        category: str,
        *,
        builtin_only: bool = False,
    ) -> frozenset[str]:
        """Expand a category prefix into all matching action types.

        Args:
            category: A category prefix (e.g. ``"code"``).
            builtin_only: Omit operator-registered custom types. A grant
                written as a bare category cannot have meant a type that
                did not exist when it was written, so the side that widens
                privilege asks for this and the side that restricts does
                not.

        Returns:
            All action types under that category. Returns an empty
            frozenset if the category has no built-in types (custom
            types under unknown categories are included unless
            *builtin_only*).
        """
        builtin = _CATEGORY_MAP.get(category, frozenset())
        if builtin_only:
            return builtin
        custom = frozenset(
            ct for ct in self._custom_types if ct.split(":")[0] == category
        )
        return builtin | custom

    @staticmethod
    def get_category(action_type: str) -> str:
        """Extract the category prefix from an action type string.

        Args:
            action_type: A ``category:action`` string.

        Returns:
            The category prefix before the first ``:``.

        Raises:
            ValueError: If the string does not contain ``:``.
        """
        if action_type.count(":") != 1:
            msg = (
                f"Action type {action_type!r} must use 'category:action' "
                "format (exactly one ':')"
            )
            raise ValueError(msg)
        category, action = action_type.split(":")
        if not category or not action:
            msg = (
                f"Action type {action_type!r} must have non-empty "
                "category and action parts"
            )
            raise ValueError(msg)
        return category

    def all_types(self) -> frozenset[str]:
        """Return all registered action types (built-in + custom)."""
        return self._all_types
