"""Content-addressed signature binding an approval to a specific call.

A sensitive external call stores its signature in the approval's metadata at
park time. On resume the tool recomputes the signature for the re-issued call
and matches it against APPROVED, unconsumed approvals, so a grant authorises
exactly the call it was approved for (not a replay or a different call).
"""

import hashlib
import json

from pydantic import BaseModel, ConfigDict

_SIGNATURE_METADATA_KEY = "external_api_signature"


def _hash(value: str | None) -> str:
    """Stable SHA-256 hex digest of *value* (``""`` sentinel for None)."""
    payload = "\x00NONE\x00" if value is None else value
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApprovalSignature(BaseModel):
    """Immutable fingerprint of a governed external call.

    Equality over the resolved request shape: connection, method, the
    resolved URL, a hash of the body, and a hash of the agent-supplied
    request headers (credentials are injected later and excluded, so an
    approval cannot be invalidated by a credential rotation).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    connection: str
    method: str
    resolved_url: str
    body_hash: str
    headers_hash: str

    @classmethod
    def build(
        cls,
        *,
        connection: str,
        method: str,
        resolved_url: str,
        body: str | None,
        headers: dict[str, str],
    ) -> ApprovalSignature:
        """Construct a signature from the resolved call components."""
        canonical_headers = json.dumps(
            sorted((k.lower(), v) for k, v in headers.items()),
            separators=(",", ":"),
        )
        return cls(
            connection=connection,
            method=method,
            resolved_url=resolved_url,
            body_hash=_hash(body),
            headers_hash=_hash(canonical_headers),
        )

    def to_metadata(self) -> dict[str, str]:
        """Serialise to an approval-metadata fragment."""
        return {_SIGNATURE_METADATA_KEY: self.model_dump_json()}

    @classmethod
    def from_metadata(cls, metadata: dict[str, str]) -> ApprovalSignature | None:
        """Parse a signature from approval metadata, or ``None`` if absent/invalid."""
        raw = metadata.get(_SIGNATURE_METADATA_KEY)
        if raw is None:
            return None
        try:
            return cls.model_validate_json(raw)
        except ValueError:
            return None

    def matches(self, other: ApprovalSignature | None) -> bool:
        """Whether *other* is an identical call signature."""
        return other is not None and self == other
