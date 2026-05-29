"""Hash chain for append-only tamper-evident audit trail."""

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChainEntry(BaseModel):
    """A single entry in the append-only hash chain.

    Attributes:
        position: Zero-based position in the chain.
        event_hash: SHA-256 hex digest of the serialized event.
        previous_hash: Hash of the prior entry (``"genesis"`` for first).
        canonical_payload: Serialized event bytes used for signing.
        signature: Raw signature bytes over the event data.
        timestamp: When the entry was created.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    position: int = Field(ge=0, description="Chain position")
    event_hash: str = Field(description="SHA-256 of event data")
    previous_hash: str = Field(description="Hash of prior entry")
    canonical_payload: bytes = Field(
        description="Canonical event bytes used for signing",
    )
    signature: bytes = Field(description="Signature over event data")
    timestamp: datetime = Field(description="Entry creation time")


class HashChain:
    """Append-only hash chain for audit trail integrity.

    Each entry links to the previous via a hash chain.  Integrity
    verification walks the chain and re-computes hashes.

    Args:
        initial_hash: Genesis hash for the first entry.
    """

    def __init__(self, initial_hash: str = "genesis") -> None:
        self._entries: list[ChainEntry] = []
        self._initial_hash = initial_hash
        self._tail_hash = initial_hash

    @property
    def initial_hash(self) -> str:
        """Genesis hash configured at construction."""
        return self._initial_hash

    @property
    def entries(self) -> tuple[ChainEntry, ...]:
        """Read-only view of all chain entries."""
        return tuple(self._entries)

    @property
    def tail_hash(self) -> str:
        """Hash of the most recent entry (or genesis)."""
        return self._tail_hash

    def snapshot(self) -> HashChain:
        """Create a read-only copy of this chain.

        Returns:
            A new ``HashChain`` with a copy of all current entries and the
            tail hash, safe for read-only inspection.
        """
        chain = HashChain(initial_hash=self._initial_hash)
        chain._entries = list(self._entries)
        chain._tail_hash = self._tail_hash
        return chain

    def append(
        self,
        event_data: bytes,
        signature: bytes,
        timestamp: datetime,
    ) -> ChainEntry:
        """Append a new entry to the chain.

        Args:
            event_data: Serialized event bytes.
            signature: Signature over event_data.
            timestamp: Entry timestamp.

        Returns:
            The newly created chain entry.
        """
        event_hash = hashlib.sha256(event_data).hexdigest()
        # Chain hash links this entry to the previous, including all
        # entry fields for tamper evidence.
        chained_hash = self._link_hash(
            self._tail_hash,
            event_hash,
            signature,
            timestamp,
        )

        entry = ChainEntry(
            position=len(self._entries),
            event_hash=event_hash,
            previous_hash=self._tail_hash,
            canonical_payload=event_data,
            signature=signature,
            timestamp=timestamp,
        )
        self._entries.append(entry)
        self._tail_hash = chained_hash
        return entry

    @staticmethod
    def _link_hash(
        previous_hash: str,
        event_hash: str,
        signature: bytes,
        timestamp: datetime,
    ) -> str:
        """Compute the chain link hash over all entry fields.

        Returns:
            The SHA-256 hex digest over the four entry fields, used as
            the new tail hash.
        """
        chain_input = b"|".join(
            (
                previous_hash.encode(),
                event_hash.encode(),
                signature.hex().encode(),
                timestamp.isoformat().encode(),
            )
        )
        return hashlib.sha256(chain_input).hexdigest()

    def verify_integrity(self) -> bool:
        """Verify the entire chain's hash integrity.

        Returns:
            ``True`` if the chain is intact, ``False`` if any link
            is broken.
        """
        expected_prev = self._initial_hash
        for entry in self._entries:
            if entry.previous_hash != expected_prev:
                return False
            expected_prev = self._link_hash(
                expected_prev,
                entry.event_hash,
                entry.signature,
                entry.timestamp,
            )
        return expected_prev == self._tail_hash
