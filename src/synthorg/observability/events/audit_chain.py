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
AUDIT_CHAIN_CALLBACK_ERROR: Final[str] = "audit_chain.callback_error"

# Unrecognized stdlib LogRecord shape that did not match any of the
# event-name extractor's known patterns (str / dict / tuple). Signals a
# logging-bridge change or an unexpected logger call site; the record
# is dropped rather than silently passed through.
AUDIT_CHAIN_RECORD_SHAPE_UNKNOWN: Final[str] = "audit_chain.record_shape_unknown"

# Config validation
AUDIT_CHAIN_CONFIG_INVALID_PRESET: Final[str] = "audit_chain.config.invalid_preset"
