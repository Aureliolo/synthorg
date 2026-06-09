"""Content hash computation for versioned Pydantic models.

Produces a deterministic SHA-256 hex digest from the canonical JSON
serialization of any frozen Pydantic model.  The hash is stable across:

- Field definition order changes (``json.dumps(sort_keys=True)``)
- Pydantic model dump mode (``mode="json"`` gives serializable primitives)
- UUID, date, enum representations (stable under ``mode="json"``)

The same technique is used in ``security/service.py`` for argument
deduplication.
"""

import hashlib
import json

from pydantic import BaseModel


def compute_content_hash(model: BaseModel) -> str:
    """Compute the SHA-256 hex digest of a Pydantic model's canonical JSON.

    The digest is deterministic: identical field values always produce
    the same hash regardless of field definition order in the class.

    Args:
        model: Any Pydantic model instance.

    Returns:
        A 64-character lowercase hexadecimal SHA-256 digest string.
    """
    canonical = json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def compute_text_hash(text: str) -> str:
    """Compute the SHA-256 hex digest of a unicode string.

    Used by the knowledge substrate to hash raw source bytes and
    per-chunk text so re-ingestion can short-circuit unchanged sources
    and re-embed only changed chunks.

    Args:
        text: Arbitrary unicode text.

    Returns:
        A 64-character lowercase hexadecimal SHA-256 digest string.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
