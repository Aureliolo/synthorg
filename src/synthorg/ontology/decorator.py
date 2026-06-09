"""``@ontology_entity`` decorator for auto-deriving entity definitions.

Decorating a Pydantic ``BaseModel`` subclass with ``@ontology_entity``
registers the model in a module-level registry for startup discovery.
Entity definitions are derived lazily (on first access via
``get_entity_registry()``) to avoid circular imports through
``synthorg.core``.
"""

import inspect
import textwrap
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final, NamedTuple, overload

from pydantic import BaseModel

from synthorg.observability import get_logger
from synthorg.observability.events.ontology import (
    ONTOLOGY_ENTITY_DECORATOR_REGISTERED,
)
from synthorg.ontology.errors import OntologyDuplicateError
from synthorg.ontology.models import (
    EntityDefinition,
    EntitySource,
    EntityTier,
)

logger = get_logger(__name__)


class _RegistryEntry(NamedTuple):
    """Raw registration data -- no ontology.models dependency."""

    cls: type[BaseModel]
    entity_name: str
    tier: str  # EntityTier value (e.g. "core")
    source: str  # EntitySource value (e.g. "auto")


_RAW_REGISTRY: dict[str, _RegistryEntry] = {}
_CACHE: dict[str, EntityDefinition] | None = None
_REGISTRY_LOCK: Final[threading.Lock] = threading.Lock()


def get_entity_registry() -> MappingProxyType[str, EntityDefinition]:
    """Return a read-only view of all registered entity definitions.

    Builds ``EntityDefinition`` objects lazily on first call and
    caches the result.  The cache is invalidated by
    ``clear_entity_registry()``.

    Holds ``_REGISTRY_LOCK`` across both the cache check and the
    rebuild so a concurrent ``_do_register()`` cannot mutate
    ``_RAW_REGISTRY`` mid-iteration (which would raise
    ``RuntimeError: dictionary changed size during iteration``).
    """
    global _CACHE  # noqa: PLW0603
    with _REGISTRY_LOCK:
        # _CACHE is set to None by _do_register() and clear_entity_registry().
        if _CACHE is None:
            _CACHE = {
                name: _derive_definition(entry) for name, entry in _RAW_REGISTRY.items()
            }
        return MappingProxyType(_CACHE)


def clear_entity_registry() -> None:
    """Clear the entity registry (for testing only).

    Holds ``_REGISTRY_LOCK`` so the clear and the cache invalidation
    happen atomically with respect to ``_do_register()`` and
    ``get_entity_registry()``.
    """
    global _CACHE  # noqa: PLW0603
    with _REGISTRY_LOCK:
        _RAW_REGISTRY.clear()
        _CACHE = None


def _derive_definition(entry: _RegistryEntry) -> EntityDefinition:
    """Introspect a Pydantic model and build an EntityDefinition.

    Returns:
        An ``EntityDefinition`` derived from the model's docstring,
        described fields, tier, and source.
    """
    from synthorg.ontology.models import (  # noqa: PLC0415
        EntityDefinition,
        EntityField,
        EntitySource,
        EntityTier,
    )

    cls = entry.cls
    name = entry.entity_name

    # Extract definition text from docstring.
    raw_doc = cls.__doc__
    definition = textwrap.dedent(raw_doc).strip() if raw_doc else name

    # Extract fields that have descriptions.
    fields: list[EntityField] = []
    for field_name, field_info in cls.model_fields.items():
        desc = field_info.description
        if not desc:
            continue

        annotation = cls.model_fields[field_name].annotation
        type_hint = _annotation_to_str(annotation) if annotation is not None else "Any"

        fields.append(
            EntityField(
                name=field_name,
                type_hint=type_hint,
                description=desc,
            ),
        )

    now = datetime.now(UTC)
    return EntityDefinition(
        name=name,
        tier=EntityTier(entry.tier),
        source=EntitySource(entry.source),
        definition=definition,
        fields=tuple(fields),
        created_by="system",
        created_at=now,
        updated_at=now,
    )


def _annotation_to_str(annotation: object) -> str:
    """Convert a type annotation to a readable string.

    Returns:
        A readable string form of the annotation (generic args
        recursively rendered; falls back to ``str(annotation)``).
    """
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        args = getattr(annotation, "__args__", ())
        args_str = ", ".join(_annotation_to_str(a) for a in args)
        origin_name = getattr(origin, "__name__", str(origin))
        return f"{origin_name}[{args_str}]" if args else origin_name
    if inspect.isclass(annotation):
        return annotation.__name__
    return str(annotation)


# Parameterise over the decorated class so callers see the concrete
# subclass type rather than ``type[BaseModel]``. Without inline type
# parameters, every ``@ontology_entity``-decorated class loses its
# identity in static type-checkers (Pyright surfaces this; mypy is
# more lenient with decorators that return ``Any``) and attribute
# access on instances returned by typed APIs (e.g. the result of
# ``await eng.create_task(...)`` which is annotated to return
# ``Task``) resolves against the bare ``BaseModel``, producing
# false-positive "no attribute" diagnostics.
@overload
def ontology_entity[T: BaseModel](cls: type[T], /) -> type[T]: ...


@overload
def ontology_entity[T: BaseModel](
    *,
    entity_name: str | None = None,
    tier: EntityTier | None = None,
    source: EntitySource | None = None,
) -> Callable[[type[T]], type[T]]: ...


def ontology_entity(
    cls: type[BaseModel] | None = None,
    /,
    *,
    entity_name: str | None = None,
    tier: EntityTier | None = None,
    source: EntitySource | None = None,
) -> type[BaseModel] | Callable[[type[BaseModel]], type[BaseModel]]:
    """Decorator to register a Pydantic model as an ontology entity.

    Can be used with or without arguments::

        @ontology_entity
        class Task(BaseModel): ...


        @ontology_entity(entity_name="Approval")
        class ApprovalItem(BaseModel): ...

    Args:
        cls: The model class (when used without parentheses).
        entity_name: Override the entity name (defaults to class name).
        tier: Entity protection tier (default: CORE).
        source: Entity origin source (default: AUTO).

    Returns:
        When used without arguments, the original class (unchanged).
        When called with keyword arguments, a single-argument decorator
        that registers and returns the class it is applied to.

    Raises:
        OntologyDuplicateError: If an entity with the same name is
            already registered.
    """
    tier_val = tier.value if tier is not None else "core"
    source_val = source.value if source is not None else "auto"

    def _do_register(target_cls: type[BaseModel]) -> type[BaseModel]:
        global _CACHE  # noqa: PLW0603
        name = entity_name or target_cls.__name__
        with _REGISTRY_LOCK:
            if name in _RAW_REGISTRY:
                msg = f"Entity '{name}' is already registered"
                raise OntologyDuplicateError(msg)
            _RAW_REGISTRY[name] = _RegistryEntry(
                cls=target_cls,
                entity_name=name,
                tier=tier_val,
                source=source_val,
            )
            _CACHE = None  # Invalidate cache.
        logger.debug(
            ONTOLOGY_ENTITY_DECORATOR_REGISTERED,
            entity_name=name,
            cls=target_cls.__qualname__,
        )
        return target_cls

    if cls is not None:
        return _do_register(cls)
    return _do_register
