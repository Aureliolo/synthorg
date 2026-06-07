"""Deterministic UUID helpers for tests.

Domain entity PKs are typed ``UUID`` while their foreign-key reference
fields stay ``NotBlankStr`` holding the canonical UUID string. A test
that threads one identifier from a PK through an FK and into a wire
assertion therefore needs the same value in two shapes. ``as_uuid``
maps a readable label to a stable ``UUID`` (so ``as_uuid("task-1")``
is the PK), and ``sid`` returns that same UUID as its canonical string
(so ``sid("task-1")`` is the FK / JSON value). Using a label keeps the
relationship legible where a bare ``uuid4()`` would obscure it.
"""

from uuid import NAMESPACE_OID, UUID, uuid5

_TEST_ID_NAMESPACE: UUID = uuid5(NAMESPACE_OID, "synthorg.tests.ids")


def as_uuid(label: str) -> UUID:
    """Return a stable ``UUID`` derived from *label*.

    Args:
        label: Readable key naming the identifier within a test.

    Returns:
        A deterministic ``UUID`` unique to *label*.
    """
    return uuid5(_TEST_ID_NAMESPACE, label)


def sid(label: str) -> str:
    """Return :func:`as_uuid` of *label* as its canonical string.

    Use for foreign-key fields and wire-shape assertions, which hold
    the UUID as a string rather than a ``UUID`` instance.

    Args:
        label: Readable key naming the identifier within a test.

    Returns:
        ``str(as_uuid(label))``.
    """
    return str(as_uuid(label))


def coerce_id(value: object) -> str:
    """Return a canonical UUID string for a test id *value*.

    Idempotent: a ``UUID`` instance or an already-canonical UUID string
    passes through unchanged, while a readable label (``"sub-a"``) is
    mapped deterministically via :func:`sid`. Test fixture helpers thread
    readable subtask / task labels that must surface as canonical UUID
    strings wherever they cross into a typed PK or join an FK against
    ``str(entity.id)``; this performs that mapping once, consistently, so
    the same label always yields the same id on both sides of a join.

    Args:
        value: A ``UUID``, a canonical UUID string, or a readable label.

    Returns:
        The canonical UUID string for *value*.

    Raises:
        TypeError: If *value* is neither a ``UUID`` nor a ``str``.
    """
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError:
            return sid(value)
    msg = f"coerce_id expects a UUID or str, got {type(value).__name__}"
    raise TypeError(msg)
