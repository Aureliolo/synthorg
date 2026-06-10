"""Deterministic UUID helpers for tests.

An entity primary key is a ``UUID``; a foreign-key reference to that
entity, and its JSON wire form, is the canonical UUID string. A test
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


def as_pk(value: object) -> UUID:
    """Return the typed-``UUID`` primary key for a test id *value*.

    Companion to :func:`coerce_id` (which returns the canonical string for
    foreign-key / wire shapes): use ``as_pk`` for a typed ``UUID`` primary-key
    field in a direct model constructor, where a bare label or canonical
    string would fail static type-checking even though Pydantic coerces it at
    runtime. Accepts the same inputs as :func:`coerce_id` -- a readable label,
    a canonical UUID string, or a ``UUID`` -- so a fixture may thread either
    shape without re-hashing an already-canonical id.

    Args:
        value: A ``UUID``, a canonical UUID string, or a readable label.

    Returns:
        The ``UUID`` primary key for *value*.
    """
    return UUID(coerce_id(value))


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
