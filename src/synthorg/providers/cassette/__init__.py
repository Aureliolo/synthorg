"""Provider-layer cassette: deterministic record / replay seam.

Records the exact provider responses of a run keyed by request, then
replays them for byte-identical, zero-LLM-spend re-execution. Integrated
at the provider chokepoint (:class:`BaseCompletionProvider`) so record /
replay is a provider-layer concern, not per-driver.
"""

from .errors import (
    CassetteError,
    CassetteFormatError,
    CassetteInternalError,
    CassetteReplayExhaustedError,
    CassetteReplayMissError,
    provider_error_for,
)
from .keying import CassetteMethod, CassetteRequestKey, request_hash
from .mode import CassetteConfig, CassetteMode
from .provider import CassetteCompletionProvider
from .redaction import (
    REDACTION_PLACEHOLDER,
    CassetteRedactor,
    NullRedactor,
    PatternRedactor,
)
from .store import (
    CASSETTE_FORMAT_VERSION,
    CassetteDocument,
    CassetteInteraction,
    CassetteOutcome,
    CassetteOutcomeKind,
    CassetteRecordedError,
    CassetteSession,
)

__all__ = [
    "CASSETTE_FORMAT_VERSION",
    "REDACTION_PLACEHOLDER",
    "CassetteCompletionProvider",
    "CassetteConfig",
    "CassetteDocument",
    "CassetteError",
    "CassetteFormatError",
    "CassetteInteraction",
    "CassetteInternalError",
    "CassetteMethod",
    "CassetteMode",
    "CassetteOutcome",
    "CassetteOutcomeKind",
    "CassetteRecordedError",
    "CassetteRedactor",
    "CassetteReplayExhaustedError",
    "CassetteReplayMissError",
    "CassetteRequestKey",
    "CassetteSession",
    "NullRedactor",
    "PatternRedactor",
    "provider_error_for",
    "request_hash",
]
