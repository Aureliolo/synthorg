"""Trace subsystem configuration.

``TraceConfig`` is a discriminated union keyed on ``kind``:

* ``"disabled"`` (default) -- tracing is off; helpers return no-op
  spans so call sites never pay for span construction.
* ``"otlp_http"`` -- emits spans via OTLP/HTTP (protobuf) to an
  OpenTelemetry collector endpoint.

New backends (gRPC, ConsoleSpanExporter for local debugging, etc.)
can be added as new ``kind`` variants without touching call sites.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

# HTTP header values must not contain CR/LF (prevents header injection
# via operator-supplied headers like auth tokens).
_FORBIDDEN_HEADER_CHARS = ("\r", "\n")


class DisabledTraceConfig(BaseModel):
    """Tracing disabled. Call-site helpers become zero-cost no-ops."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    kind: Literal["disabled"] = "disabled"


class OtlpHttpTraceConfig(BaseModel):
    """Exports OTel spans via OTLP/HTTP (protobuf).

    Attributes:
        endpoint: Collector URL (e.g. ``"http://otel-collector:4318"``).
            The exporter appends ``/v1/traces`` automatically.
        headers: Extra HTTP headers as ``(name, value)`` pairs. Use
            for auth tokens (e.g. ``"x-api-key"`` or a vendor-specific
            ``"x-custom-auth"`` header).
        sampling_ratio: Fraction of traces to record (0.0-1.0).
            1.0 records every trace; 0.0 records none. Use sampling
            to bound cost at high trace volumes.
        batch_max_queue_size: Upper bound on pending spans before
            new spans are dropped (back-pressure).
        batch_max_export_batch_size: Max spans per exported batch.
        batch_export_timeout_sec: Per-export HTTP timeout.
        schedule_delay_sec: Interval between batch flushes.
        service_name: Value for OTel resource ``service.name``
            attribute. Identifies this process in waterfalls.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    kind: Literal["otlp_http"] = "otlp_http"
    endpoint: NotBlankStr
    headers: tuple[tuple[NotBlankStr, NotBlankStr], ...] = ()
    sampling_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    batch_max_queue_size: int = Field(default=2048, ge=1)
    batch_max_export_batch_size: int = Field(default=512, ge=1)
    batch_export_timeout_sec: float = Field(default=30.0, gt=0)
    schedule_delay_sec: float = Field(default=5.0, gt=0)
    service_name: NotBlankStr = "synthorg"

    @model_validator(mode="after")
    def _reject_header_injection(self) -> OtlpHttpTraceConfig:
        r"""Reject header names/values containing CR/LF.

        Operator-supplied headers commonly carry auth tokens read
        from config files or env vars. A stray ``\n`` would let a
        malicious value inject additional headers or split the
        request -- cheap to prevent at config-load time.
        """
        for name, value in self.headers:
            for field_name, field_value in (("name", name), ("value", value)):
                if any(ch in field_value for ch in _FORBIDDEN_HEADER_CHARS):
                    msg = (
                        f"OTLP header {field_name} contains CR/LF "
                        "(forbidden; prevents HTTP header injection)"
                    )
                    raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_batch_sizes(self) -> OtlpHttpTraceConfig:
        """Reject batch sizes that the BatchSpanProcessor would reject.

        OpenTelemetry's ``BatchSpanProcessor`` requires
        ``max_export_batch_size <= max_queue_size``. Catching this
        at config-load time gives a clear error instead of an
        opaque SDK exception at trace-handler build time.
        """
        if self.batch_max_export_batch_size > self.batch_max_queue_size:
            msg = (
                "batch_max_export_batch_size "
                f"({self.batch_max_export_batch_size}) must not exceed "
                f"batch_max_queue_size ({self.batch_max_queue_size})"
            )
            raise ValueError(msg)
        return self


TraceConfig = Annotated[
    DisabledTraceConfig | OtlpHttpTraceConfig,
    Field(discriminator="kind"),
]
