"""Audit chain sink internal event constants.

These events intentionally use the ``audit_chain.*`` prefix rather
than ``security.*`` so that logs produced from inside the sink's own
:meth:`AuditChainSink.emit` error paths cannot loop back into the
handler and recurse on the single-worker signing executor. Every
other audit-chain event (signatures, timestamping, integrity checks)
still lives under ``security.*`` because those DO need to be audited.
"""

from typing import Final

AUDIT_CHAIN_EMIT_ERROR: Final[str] = "audit_chain.emit_error"
AUDIT_CHAIN_EMIT_TIMEOUT: Final[str] = "audit_chain.emit_timeout"
AUDIT_CHAIN_EMIT_VALIDATION_FAILED: Final[str] = "audit_chain.emit_validation_failed"
"""Boundary validation rejected the assembled payload before signing.

Distinct from :data:`AUDIT_CHAIN_EMIT_ERROR` so operators can tell a
schema-rejected event apart from a signing/serialization failure.
``parse_typed`` already emits :data:`API_BOUNDARY_VALIDATION_FAILED`
with the structured boundary detail; this event is the audit-chain
side of the same incident, recording that the chain dropped the
event without appending it.
"""
AUDIT_CHAIN_CALLBACK_ERROR: Final[str] = "audit_chain.callback_error"

# Unrecognized stdlib LogRecord shape that did not match any of the
# event-name extractor's known patterns (str / dict / tuple). Signals a
# logging-bridge change or an unexpected logger call site; the record
# is dropped rather than silently passed through.
AUDIT_CHAIN_RECORD_SHAPE_UNKNOWN: Final[str] = "audit_chain.record_shape_unknown"

# Config validation
AUDIT_CHAIN_CONFIG_INVALID_PRESET: Final[str] = "audit_chain.config.invalid_preset"

# Signer key management + sink installation (audit_chain.* prefix so a
# failure logged here cannot recurse into the sink's own emit path).
AUDIT_CHAIN_SIGNER_KEY_LOADED: Final[str] = "audit_chain.signer.key_loaded"
AUDIT_CHAIN_SIGNER_KEY_GENERATED: Final[str] = "audit_chain.signer.key_generated"
AUDIT_CHAIN_SINK_INSTALLED: Final[str] = "audit_chain.sink.installed"
AUDIT_CHAIN_SINK_DISABLED: Final[str] = "audit_chain.sink.disabled"
