"""Cassette data model: the on-disk document and its interaction records.

Split out of :mod:`store` so that module is just the session engine
(lanes, FIFO cursors, atomic persistence). These are pure, frozen data
types plus the body-digest helper that backs the cassette's
self-integrity header. ``store`` re-exports every public name here, so
external importers keep importing them from ``cassette.store``.
"""

import hashlib
import json
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.models import (
    CompletionResponse,
    StreamChunk,
)

from .keying import CassetteMethod


class CassetteOutcomeKind(StrEnum):
    """Which payload an interaction recorded."""

    RESPONSE = "response"
    ERROR = "error"
    STREAM = "stream"
    CAPABILITIES = "capabilities"


class CassetteRecordedError(BaseModel):
    """A provider error captured for faithful replay.

    ``message`` is already scrubbed via ``safe_error_description`` at
    the recording boundary; it is safe to persist verbatim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error_class: NotBlankStr = Field(description="Recorded type(exc).__name__")
    message: str = Field(description="Scrubbed error description")
    context: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Redacted ProviderError.context for faithful replay",
    )


class CassetteOutcome(BaseModel):
    """The recorded result of one provider call.

    Exactly one payload field is populated, selected by ``kind``. The
    outcome is stored **verbatim** (never redacted): it is the
    byte-identical replay artefact. Redaction applies only to the
    request copy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CassetteOutcomeKind = Field(description="Outcome discriminator")
    response: CompletionResponse | None = Field(default=None)
    error: CassetteRecordedError | None = Field(default=None)
    stream_chunks: tuple[StreamChunk, ...] | None = Field(default=None)
    capabilities: ModelCapabilities | None = Field(default=None)

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> Self:
        """Ensure the payload for ``kind`` is set and others are not.

        A ``STREAM`` outcome may additionally carry an ``error``: a
        terminal :class:`ProviderError` raised *after* some chunks were
        already emitted, so replay can re-emit those chunks faithfully
        and only then re-raise.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the payload field required by ``kind`` is
                ``None``, or a payload field for a different ``kind`` is
                set (except a ``STREAM`` outcome carrying an ``error``).
        """
        by_kind: dict[CassetteOutcomeKind, object] = {
            CassetteOutcomeKind.RESPONSE: self.response,
            CassetteOutcomeKind.ERROR: self.error,
            CassetteOutcomeKind.STREAM: self.stream_chunks,
            CassetteOutcomeKind.CAPABILITIES: self.capabilities,
        }
        for kind, value in by_kind.items():
            populated = value is not None
            if kind is self.kind and not populated:
                msg = f"{self.kind.value} outcome must set its payload"
                raise ValueError(msg)
            if kind is not self.kind and populated:
                if (
                    self.kind is CassetteOutcomeKind.STREAM
                    and kind is CassetteOutcomeKind.ERROR
                ):
                    # Terminal stream error after partial output.
                    continue
                msg = f"{self.kind.value} outcome must not set {kind.value}"
                raise ValueError(msg)
        return self

    @classmethod
    def from_response(cls, response: CompletionResponse) -> Self:
        """Build a response outcome.

        Returns:
            A ``CassetteOutcome`` with ``kind=RESPONSE`` wrapping the
            given ``CompletionResponse``.
        """
        return cls(kind=CassetteOutcomeKind.RESPONSE, response=response)

    @classmethod
    def from_error(
        cls,
        *,
        error_class: str,
        message: str,
        context: dict[str, JsonValue] | None = None,
    ) -> Self:
        """Build an error outcome from a scrubbed description.

        ``context`` is the (already scrubbed) ``ProviderError.context``;
        it is persisted so a replayed exception carries the original
        payload that callers may branch on.

        Returns:
            A ``CassetteOutcome`` with ``kind=ERROR`` populated from the
            scrubbed description and context.
        """
        return cls(
            kind=CassetteOutcomeKind.ERROR,
            error=CassetteRecordedError(
                error_class=error_class,
                message=message,
                context=context or {},
            ),
        )

    @classmethod
    def from_stream(
        cls,
        chunks: tuple[StreamChunk, ...],
        *,
        error: CassetteRecordedError | None = None,
    ) -> Self:
        """Build a stream outcome from the recorded chunk sequence.

        ``error`` records a terminal :class:`ProviderError` raised after
        the recorded chunks were emitted, so replay re-emits the chunks
        and only then re-raises.

        Returns:
            A ``CassetteOutcome`` with ``kind=STREAM`` wrapping the chunk
            tuple and any terminal ``error``.
        """
        return cls(
            kind=CassetteOutcomeKind.STREAM,
            stream_chunks=chunks,
            error=error,
        )

    @classmethod
    def from_capabilities(cls, capabilities: ModelCapabilities) -> Self:
        """Build a capability-lookup outcome.

        Returns:
            A ``CassetteOutcome`` with ``kind=CAPABILITIES`` wrapping the
            given ``ModelCapabilities``.
        """
        return cls(
            kind=CassetteOutcomeKind.CAPABILITIES,
            capabilities=capabilities,
        )


class CassetteInteraction(BaseModel):
    """One recorded provider call: request key + verbatim outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: CassetteMethod = Field(description="Provider method")
    request_hash: NotBlankStr = Field(description="Canonical request hash")
    lane: int = Field(ge=0, description="Per-task FIFO lane ordinal")
    seq: int = Field(ge=0, description="FIFO index within (hash, lane)")
    # ``object``, not ``JsonValue``: a never-replayed human copy holding the
    # redactor's ``object`` output (unlike the replayed, JsonValue context).
    request_repr: dict[str, object] = Field(
        default_factory=dict,
        description="Redacted human-readable request copy (never replayed)",
    )
    outcome: CassetteOutcome = Field(description="Verbatim recorded outcome")


def body_digest(interactions: tuple[CassetteInteraction, ...]) -> str:
    """Return the sha256 over the canonical JSON of *interactions*.

    The digest covers the interactions payload only (never the header that
    stores it), so a recompute on load is comparable to the recorded value.

    Returns:
        The hex sha256 of the canonically-serialised interactions list.
    """
    payload = json.dumps(
        [interaction.model_dump(mode="json") for interaction in interactions],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CassetteDocument(BaseModel):
    """On-disk cassette: a format version + ordered interactions.

    ``body_sha256`` is a self-integrity header: the sha256 over the canonical
    interactions payload, written at record time and re-verified on load so a
    file edited or corrupted after recording is refused rather than replayed.
    It is ``None`` only on a document built in memory and never persisted; a
    cassette read from disk in replay mode must carry it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cassette_format_version: int = Field(description="Schema version")
    body_sha256: NotBlankStr | None = Field(
        default=None, description="sha256 over the canonical interactions payload"
    )
    interactions: tuple[CassetteInteraction, ...] = Field(default=())
