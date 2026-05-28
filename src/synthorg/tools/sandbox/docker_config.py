"""Docker sandbox configuration model."""

import re
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.config import (
    CONFIG_VALIDATION_FAILED,
)
from synthorg.tools.sandbox._image_resolution import (
    get_resolved_sandbox_image,
    get_resolved_sidecar_image,
)
from synthorg.tools.sandbox.config import DEFAULT_ENV_DENYLIST_PATTERNS
from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.network_presets import PRESETS
from synthorg.tools.sandbox.policy import SandboxPolicy

logger = get_logger(__name__)

_VALID_NETWORK_MODES = frozenset({"none", "bridge", "host"})
_MIN_PORT = 1
_MAX_PORT: Final[int] = 65535
_HOST_PORT_PARTS: Final[int] = 2

# Docker tmpfs size syntax: positive integer, optional k/m/g suffix
# (case-insensitive).  Rejects leading zeros, negatives, and unknown
# suffixes so malformed values fail at config-load time rather than
# surfacing as opaque Docker API errors at container creation.
_TMPFS_SIZE_PATTERN = re.compile(r"^[1-9]\d*[kmg]?$", re.IGNORECASE)


def _default_sandbox_image() -> str:
    """Resolve the default sandbox image from the resolution cache.

    The cache is populated at startup by ``_apply_bridge_config``
    after resolving ``tools.sandbox_image`` through ``ConfigResolver``
    (which honours the canonical DB > env > YAML > default chain via
    ``env_var_override="SYNTHORG_SANDBOX_IMAGE"``). Tests outside the
    lifecycle path get the documented fallback constant.

    Returns:
        Result of type ``str``.
    """
    return get_resolved_sandbox_image()


def _default_sidecar_image() -> str:
    """Resolve the default sidecar image from the resolution cache.

    Same resolution path as :func:`_default_sandbox_image`.

    Returns:
        Result of type ``str``.
    """
    return get_resolved_sidecar_image()


class DockerSandboxConfig(BaseModel):
    """Configuration for the Docker sandbox backend.

    Attributes:
        image: Docker image to use for sandbox containers.
        network: Default Docker network mode.
        network_overrides: Per-category network mode overrides.
        allowed_hosts: Host:port allowlist for network filtering.
        dns_allowed: Allow outbound DNS when ``allowed_hosts`` restricts
            network.  Default ``True`` (needed for hostname resolution).
            Set to ``False`` to require IP addresses in ``allowed_hosts``.
        loopback_allowed: Allow loopback traffic in restricted network
            mode.  Default ``True``.
        memory_limit: Container memory limit (Docker format).
        cpu_limit: CPU core limit for the container.
        timeout_seconds: Default command timeout in seconds.
        mount_mode: Workspace mount mode (read-write or read-only).
        runtime: Optional container runtime (e.g. ``"runsc"`` for gVisor).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    image: NotBlankStr = Field(
        default_factory=_default_sandbox_image,
        description=(
            "Docker image for sandbox containers. Resolution chain at"
            " backend startup: DB override > SYNTHORG_SANDBOX_IMAGE env"
            " var > YAML tools.sandbox.docker.image > the documented"
            " fallback constant ghcr.io/aureliolo/synthorg-sandbox:latest."
            " ConfigResolver writes the resolved value into the"
            " sandbox image-resolution cache once at startup; this"
            " field's default_factory reads from that cache, so"
            " runtime DB overrides require a restart"
            " (read_only_post_init=True on the registered setting)."
        ),
    )
    network: Literal["none", "bridge", "host"] = Field(
        default="none",
        description="Default Docker network mode",
    )
    network_overrides: dict[NotBlankStr, NotBlankStr] = Field(
        default_factory=dict,
        description="Per-category network mode overrides",
    )
    runtime_overrides: dict[NotBlankStr, NotBlankStr] = Field(
        default_factory=dict,
        description="Per-category container runtime overrides",
    )
    allowed_hosts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Host:port allowlist for network filtering",
    )
    dns_allowed: bool = Field(
        default=True,
        description=(
            "Allow outbound DNS (port 53) when allowed_hosts restricts "
            "network; set to False to require IP addresses"
        ),
    )
    loopback_allowed: bool = Field(
        default=True,
        description="Allow loopback traffic in restricted network mode",
    )
    memory_limit: NotBlankStr = Field(
        default="512m",
        description="Container memory limit (Docker format, e.g. '512m')",
    )
    cpu_limit: float = Field(default=1.0, gt=0, le=16)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    pids_limit: int = Field(
        default=64,
        ge=1,
        le=1024,
        description=(
            "PIDs cap for the main sandbox container. Guards against "
            "fork-bomb-style runaways from untrusted tool code."
        ),
    )
    tmpfs_size: NotBlankStr = Field(
        default="64m",
        description=(
            "Size of the tmpfs mounted at /tmp inside the sandbox "
            "container (Docker tmpfs size syntax, e.g. '64m')."
        ),
    )
    sidecar_pids_limit: int = Field(
        default=32,
        ge=1,
        le=1024,
        description=(
            "PIDs cap for the network sidecar container. Tighter than "
            "the main sandbox because the sidecar only runs dnsmasq + "
            "iptables + a small Python health server."
        ),
    )
    sidecar_tmpfs_size: NotBlankStr = Field(
        default="8m",
        description=(
            "Size of the tmpfs mounted at /tmp inside the network "
            "sidecar container (Docker tmpfs size syntax, e.g. '8m')."
        ),
    )
    mount_mode: Literal["rw", "ro"] = Field(
        default="ro",
        description="Workspace mount mode (read-only by default)",
    )
    runtime: NotBlankStr | None = Field(
        default=None,
        description="Optional container runtime (e.g. 'runsc' for gVisor)",
    )
    env_denylist_patterns: tuple[str, ...] = Field(
        default=DEFAULT_ENV_DENYLIST_PATTERNS,
        description=(
            "fnmatch patterns for env-var names stripped from "
            "declaration-sourced env additions before they reach the "
            "container (secret heuristics + loader-injection vars). "
            "Explicit tool-supplied env_overrides bypass this by design."
        ),
    )
    sidecar_image: NotBlankStr = Field(
        default_factory=_default_sidecar_image,
        description=(
            "Docker image for network sidecar containers. Resolution"
            " chain at backend startup: DB override >"
            " SYNTHORG_SIDECAR_IMAGE env var > YAML"
            " tools.sandbox.docker.sidecar_image > fallback constant"
            " ghcr.io/aureliolo/synthorg-sidecar:latest. Read once at"
            " startup from the sidecar image-resolution cache; runtime"
            " DB overrides require a restart."
        ),
    )
    network_allow_all: bool = Field(
        default=False,
        description=(
            "Allow all outbound connections (bypasses allowlist). "
            "WARNING: disables network isolation."
        ),
    )
    network_presets: tuple[NotBlankStr, ...] = Field(
        default=(),
        description=(
            "Named rule presets to include (e.g. 'python-dev', 'git'). "
            "Merged with allowed_hosts at validation time."
        ),
    )
    policy: SandboxPolicy | None = Field(
        default=None,
        description=(
            "Structured 4-domain policy overlay (filesystem, network, "
            "process, inference).  Consumed by the sandbox execution "
            "layer to apply domain-specific constraints at runtime."
        ),
    )
    lifecycle: SandboxLifecycleConfig = Field(
        default_factory=SandboxLifecycleConfig,
        description=(
            "Container lifecycle strategy (per-agent, per-task, or "
            "per-call).  Controls container reuse across tool calls."
        ),
    )

    @model_validator(mode="after")
    def _validate_memory_limit(self) -> Self:
        """Validate that memory_limit uses a supported format.

        Accepts an integer with an optional ``k``/``m``/``g`` suffix.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        limit = normalize_ascii_lowercase(self.memory_limit)
        if not limit:
            msg = "Memory limit must not be empty"
            logger.warning(CONFIG_VALIDATION_FAILED, field="memory_limit", reason=msg)
            raise ValueError(msg)
        multipliers = {"k", "m", "g"}
        numeric_part = limit[:-1] if limit[-1] in multipliers else limit
        try:
            value = int(numeric_part)
        except ValueError as exc:
            msg = f"Invalid memory_limit format: {self.memory_limit!r}"
            logger.warning(CONFIG_VALIDATION_FAILED, field="memory_limit", reason=msg)
            raise ValueError(msg) from exc
        if value <= 0:
            msg = f"Memory limit must be positive, got: {self.memory_limit!r}"
            logger.warning(CONFIG_VALIDATION_FAILED, field="memory_limit", reason=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_tmpfs_sizes(self) -> Self:
        """Validate that tmpfs_size fields use Docker-compatible syntax.

        Both ``tmpfs_size`` and ``sidecar_tmpfs_size`` are interpolated
        directly into Docker tmpfs mount specs, so fail fast at config
        load time rather than surfacing as an opaque Docker API error
        during container creation.  Accepts a positive integer with an
        optional ``k``/``m``/``g`` suffix (case-insensitive); rejects
        leading zeros, negatives, zero, and unknown suffixes.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        for field_name, value in (
            ("tmpfs_size", self.tmpfs_size),
            ("sidecar_tmpfs_size", self.sidecar_tmpfs_size),
        ):
            if _TMPFS_SIZE_PATTERN.fullmatch(value.strip()) is None:
                msg = (
                    f"{field_name} must be a positive integer with an "
                    f"optional k/m/g suffix, got: {value!r}"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    field=field_name,
                    reason=msg,
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_network_overrides(self) -> Self:
        """Ensure network override values are valid network modes.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        for category, mode in self.network_overrides.items():
            if mode not in _VALID_NETWORK_MODES:
                msg = (
                    f"Invalid network mode {mode!r} for category "
                    f"{category!r}; must be one of {sorted(_VALID_NETWORK_MODES)}"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    field="network_overrides",
                    category=category,
                    reason=msg,
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="before")
    @classmethod
    def _resolve_network_presets(cls, data: Any) -> Any:
        """Resolve preset names and merge into ``allowed_hosts``.

        Runs as a before-validator so the frozen model is constructed
        with the final merged ``allowed_hosts`` -- no post-construction
        mutation needed.

        Returns:
            Result of type ``Any``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not isinstance(data, dict):
            return data
        presets = data.get("network_presets")
        if not presets:
            return data
        allowed = list(data.get("allowed_hosts", ()))
        existing = set(allowed)
        for preset_name in presets:
            if preset_name not in PRESETS:
                msg = (
                    f"Unknown network preset {preset_name!r}; "
                    f"available: {sorted(PRESETS)}"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    field="network_presets",
                    reason=msg,
                )
                raise ValueError(msg)
            for entry in PRESETS[preset_name]:
                if entry not in existing:
                    allowed.append(entry)
                    existing.add(entry)
        return {**data, "allowed_hosts": tuple(allowed)}

    @model_validator(mode="after")
    def _validate_network_allow_all(self) -> Self:
        """Reject ``network_allow_all`` with non-empty ``allowed_hosts``.

        Also rejects ``network_allow_all`` with host networking since
        the sidecar would silently override the requested network mode.

        Runs after ``_validate_network_presets`` so preset-merged
        hosts are included in the mutual exclusion check.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.network_allow_all and self.allowed_hosts:
            msg = (
                "network_allow_all=True is mutually exclusive with "
                "allowed_hosts -- set one or the other"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="network_allow_all",
                reason=msg,
            )
            raise ValueError(msg)
        has_host = self.network == "host" or any(
            v == "host" for v in self.network_overrides.values()
        )
        if self.network_allow_all and has_host:
            msg = (
                "network_allow_all=True is incompatible with "
                "network='host' -- sidecar would override host networking"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="network_allow_all",
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_allowed_hosts(self) -> Self:
        """Validate that allowed_hosts entries use ``host:port`` format.

        Only IPv4 addresses and hostnames are supported; IPv6
        addresses are not supported by the sidecar transparent
        proxy.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        for entry in self.allowed_hosts:
            parts = entry.split(":")
            if len(parts) != _HOST_PORT_PARTS:
                msg = (
                    f"allowed_hosts entry {entry!r} must use "
                    "'host:port' format (exactly one ':'); "
                    "IPv6 addresses are not supported"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    field="allowed_hosts",
                    reason=msg,
                )
                raise ValueError(msg)
            host, port_str = parts
            if not host or host == "*":
                msg = (
                    f"host part of {entry!r} must be a hostname "
                    "or IP (not empty or wildcard)"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    field="allowed_hosts",
                    reason=msg,
                )
                raise ValueError(msg)
            try:
                port = int(port_str)
            except ValueError as exc:
                msg = f"port {port_str!r} in {entry!r} is not a valid integer"
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    field="allowed_hosts",
                    reason=msg,
                )
                raise ValueError(msg) from exc
            if port < _MIN_PORT or port > _MAX_PORT:
                msg = (
                    f"port {port} in {entry!r} must be "
                    f"between {_MIN_PORT} and {_MAX_PORT}"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    field="allowed_hosts",
                    reason=msg,
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_no_allowed_hosts_with_host_network(self) -> Self:
        """Reject allowed_hosts with network='host' (unsafe).

        Checks both the top-level ``network`` field and any
        ``network_overrides`` entries.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not self.allowed_hosts:
            return self
        if self.network == "host":
            msg = (
                "allowed_hosts cannot be used with network='host' -- "
                "sidecar would affect the host network stack"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="allowed_hosts",
                reason=msg,
            )
            raise ValueError(msg)
        host_overrides = [
            cat for cat, mode in self.network_overrides.items() if mode == "host"
        ]
        if host_overrides:
            msg = (
                "allowed_hosts cannot be used with "
                "network_overrides containing 'host' "
                f"(categories: {sorted(host_overrides)}) -- "
                "sidecar would affect the host network stack"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="allowed_hosts",
                reason=msg,
            )
            raise ValueError(msg)
        return self
