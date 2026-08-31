# module-kind: code
"""What SYSTEM a pair is bound to, hashed so a swap under one name is caught.

A journal header records ``{"provider": "example-provider", "model_id":
"example-capable-001"}``. Those are placeholders, kept vendor-neutral by
design; the endpoint, the real model and the serving stack live only in
gitignored ``providers.local.yaml``, which nothing hashes today, so a provider
swap mid-matrix passes ``--resume`` undetected. Quantisation and serving stack
can differ between two hosts of the identical weights.

Shared by both harnesses in this tree, because the question ("did the thing
that answered this pair change between two runs of the same matrix") is the
same question for either of them.
"""

import hashlib
import json
from typing import Final

from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.types import NotBlankStr

#: Credential VALUES, excluded by name rather than by convenience. A key
#: rotation against the same endpoint is not a system change, and refusing a
#: resume over one would cost a whole matrix on a false positive. Everything
#: else names the system and stays in: base_url, litellm_provider, driver,
#: auth_type, billing_model, keep_alive, connection_name and the models block
#: among them.
_EXCLUDED_CREDENTIAL_VALUES: Final[frozenset[str]] = frozenset(
    {"subscription_token", "oauth_client_secret", "custom_header_value"}
)

#: ``repr=False`` fields on :class:`ProviderConfig` that are NOT credential
#: values: a reference to a catalog entry, not a secret itself. Declared
#: separately from the exclusion set above so the guard below can tell
#: "excluded on purpose" from "forgotten", rather than defaulting a new
#: ``repr=False`` field added next year to either silently entering the digest
#: or silently leaving it.
_REPR_FALSE_NOT_A_SECRET: Final[frozenset[str]] = frozenset({"connection_name"})


def _guard_declared_fields_are_real() -> None:
    """Fail at import time if a declared name is not a field.

    Raises:
        AssertionError: A name in either declared set does not name a field of
            :class:`ProviderConfig`, or a ``repr=False`` field is covered by
            neither set.
    """
    fields = ProviderConfig.model_fields
    declared = _EXCLUDED_CREDENTIAL_VALUES | _REPR_FALSE_NOT_A_SECRET
    unknown = declared - fields.keys()
    if unknown:
        msg = (
            f"connection_identity declares field(s) ProviderConfig does not "
            f"have: {sorted(unknown)}"
        )
        raise AssertionError(msg)
    repr_false = {name for name, info in fields.items() if info.repr is False}
    undeclared = repr_false - declared
    if undeclared:
        msg = (
            f"ProviderConfig field(s) {sorted(undeclared)} are repr=False but "
            f"neither excluded from the connection digest nor declared not a "
            f"secret; a credential field added silently would enter every "
            f"connection_sha256"
        )
        raise AssertionError(msg)


_guard_declared_fields_are_real()


def connection_sha256(config: ProviderConfig, *, model_id: str) -> NotBlankStr:
    """Digest the system a ``(provider, model)`` pair is actually bound to.

    Args:
        config: The connection the pair dispatches through.
        model_id: The model this pair names, folded in alongside the
            connection: the same connection serving a different model is a
            different system for this measurement's purposes.

    Returns:
        A ``sha256:``-prefixed digest, stable for one connection and model and
        comparable across a resume.
    """
    dumped = config.model_dump(mode="json")
    for excluded in _EXCLUDED_CREDENTIAL_VALUES:
        dumped.pop(excluded, None)
    dumped["model_id"] = model_id
    canonical = json.dumps(dumped, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return NotBlankStr(f"sha256:{digest}")


__all__ = ["connection_sha256"]
