"""4-domain sandbox policy model.

Provides a structured overlay for :class:`DockerSandboxConfig` that
consolidates filesystem, network, process, and inference domain
policies into a single ``SandboxPolicy`` model.  Adapted from
NVIDIA OpenShell's declarative policy engine.

When present on ``DockerSandboxConfig.policy``, domain-specific
fields override the flat configuration fields.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class FilesystemPolicy(BaseModel):
    """Filesystem access policy for sandbox containers.

    Controls which paths inside the container are readable,
    writable, or explicitly denied.

    Attributes:
        read_paths: Paths allowed for reading.
        write_paths: Paths allowed for writing.
        deny_paths: Paths explicitly denied (overrides read/write).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    read_paths: tuple[str, ...] = ("/workspace",)
    write_paths: tuple[str, ...] = ()
    deny_paths: tuple[str, ...] = ("/etc/shadow", "/root")


class NetworkPolicy(BaseModel):
    """Network access policy for sandbox containers.

    Mirrors the network-related fields from ``DockerSandboxConfig``
    under a dedicated domain model.

    Attributes:
        mode: Docker network mode (``none``, ``bridge``, ``host``).
        allowed_hosts: Host:port allowlist for network filtering.
        dns_allowed: Allow outbound DNS when hosts are restricted.
        loopback_allowed: Allow loopback traffic in restricted mode.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    mode: Literal["none", "bridge", "host"] = "none"
    allowed_hosts: tuple[NotBlankStr, ...] = ()
    dns_allowed: bool = True
    loopback_allowed: bool = True


class ProcessPolicy(BaseModel):
    """Process execution policy for sandbox containers.

    Controls the number and types of processes allowed inside
    the container.

    Attributes:
        max_processes: Maximum concurrent processes (PID limit).
        allowed_executables: Whitelist of executable paths.
            Empty tuple means allow all.
        deny_executables: Blacklist of executable paths.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_processes: int = Field(default=64, gt=0, le=4096)
    allowed_executables: tuple[str, ...] = ()
    deny_executables: tuple[str, ...] = ()


class InferencePolicy(BaseModel):
    """Inference routing policy for LLM traffic from sandboxes.

    Controls how LLM API requests originating from sandbox
    containers are handled -- either routed through the auth
    proxy (credentials never enter the sandbox) or allowed
    directly.

    Attributes:
        route_through_proxy: Route LLM traffic through auth proxy.
        allowed_providers: Provider names allowed for direct access
            (only relevant when ``route_through_proxy`` is ``False``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    route_through_proxy: bool = False
    allowed_providers: tuple[NotBlankStr, ...] = ()


class SandboxPolicy(BaseModel):
    """Consolidated 4-domain sandbox policy.

    Groups filesystem, network, process, and inference policies
    into a single structured model.  Used as an optional overlay
    on ``DockerSandboxConfig`` -- when present, domain-specific
    fields override the flat configuration fields.

    Attributes:
        filesystem: Filesystem access policy.
        network: Network access policy.
        process: Process execution policy.
        inference: Inference routing policy.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    filesystem: FilesystemPolicy = Field(
        default_factory=FilesystemPolicy,
    )
    network: NetworkPolicy = Field(
        default_factory=NetworkPolicy,
    )
    process: ProcessPolicy = Field(
        default_factory=ProcessPolicy,
    )
    inference: InferencePolicy = Field(
        default_factory=InferencePolicy,
    )
