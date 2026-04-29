"""Telemetry configuration model."""

from enum import StrEnum, unique
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import (
    NotBlankStr,  # noqa: TC001 -- Pydantic needs it at runtime
)

MAX_STRING_LENGTH: Final[int] = 64
"""Cap for telemetry string values.

Shared between :class:`synthorg.telemetry.privacy.PrivacyScrubber`,
the Docker daemon enrichment helpers in
:mod:`synthorg.telemetry.host_info`, and the ``environment`` fields
on :class:`TelemetryConfig` / :class:`TelemetryEvent`. One constant
avoids the silent-divergence hazard where a future edit raises one
cap and leaves the other unchanged, letting unexpectedly long
strings slip past downstream validation.
"""

DEFAULT_ENVIRONMENT: NotBlankStr = "dev"
"""Baseline tag used when no explicit environment is configured.

The collector resolves the effective environment from four inputs,
in order:

1. ``SYNTHORG_TELEMETRY_ENV`` -- explicit operator override, always wins.
2. Well-known CI markers (``CI``, ``GITLAB_CI``, ``BUILDKITE``,
   ``JENKINS_URL``, or any ``RUNPOD_*``) -- tags as ``ci``.
3. ``SYNTHORG_TELEMETRY_ENV_BAKED`` -- baked into the image at build
   time via ``docker/backend/Dockerfile``'s ``DEPLOYMENT_ENV`` build
   argument. Release-tag builds ship ``prod``; ``-dev.N`` pre-release
   tag builds ship ``pre-release``; everything else (main pushes,
   PR builds, local ``docker build``) ships the Dockerfile default
   ``dev``.
4. This constant -- the last-resort fallback when nothing else matched.

A non-blank default avoids ``null`` showing up as
``deployment.environment`` in Logfire, which used to make every
deployment look identical in the span stream.
"""


@unique
class TelemetryBackend(StrEnum):
    """Supported telemetry reporter backends."""

    LOGFIRE = "logfire"
    NOOP = "noop"


class TelemetryConfig(BaseModel):
    """Configuration for opt-in anonymous project telemetry.

    Telemetry is **disabled by default**. When enabled, only
    aggregate usage metrics are sent -- never API keys, chat
    content, or personal data. Telemetry is SynthOrg-owned and
    project-scoped: the write token is embedded in source and
    cannot be redirected to a different backend. Operators that
    need their own observability stack use the Postgres +
    Prometheus + audit-chain path, not this module.

    Attributes:
        enabled: Master switch (default ``False``). Can be
            overridden by the ``SYNTHORG_TELEMETRY_ENABLED`` env var.
        backend: Reporter backend to use.
        heartbeat_interval_hours: Hours between periodic heartbeat
            events.
        environment: Deployment environment tag (``local-docker``,
            ``ci``, ``prod``, ...). Rendered as OTel
            ``deployment.environment`` on the Logfire resource and
            attached to every event. Overridden at runtime by the
            ``SYNTHORG_TELEMETRY_ENV`` env var. Defaults to
            :data:`DEFAULT_ENVIRONMENT` so events are always tagged
            even when the operator has not set one.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable anonymous project telemetry (default: off)",
    )
    backend: TelemetryBackend = Field(
        default=TelemetryBackend.LOGFIRE,
        description="Telemetry reporter backend",
    )
    heartbeat_interval_hours: float = Field(
        default=6.0,
        gt=0.0,
        le=168.0,
        description="Hours between heartbeat events (1h--168h)",
    )
    # Stays :class:`NotBlankStr` (not a ``Literal``) on purpose so
    # operators can set free-form tags like ``staging-east``,
    # ``canary-v2``, or ``eu-prod`` without a code change. The 64-char
    # cap pins the value against the scrubber's string-length rule.
    environment: NotBlankStr = Field(
        default=DEFAULT_ENVIRONMENT,
        max_length=MAX_STRING_LENGTH,
        description=(
            "Deployment environment tag (local-docker / ci / prod / ...). "
            "Overridden by SYNTHORG_TELEMETRY_ENV at runtime."
        ),
    )
