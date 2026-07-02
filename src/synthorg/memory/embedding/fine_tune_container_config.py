# module-kind: code
"""HostConfig builders for ephemeral fine-tune containers.

Stage and probe containers share the sandbox-grade hardening baseline
(full capability drop, no privilege escalation, read-only rootfs with
an exec-capable ``/tmp`` tmpfs); the stage variant adds the writable
``/data`` volume bind, the operator memory limit, and optional GPU
passthrough.
"""

import re
from typing import Final

from synthorg.memory.embedding.fine_tune_models import FineTuneExecutionConfig
from synthorg.memory.errors import FineTuneStageExecutionError
from synthorg.observability import safe_error_description
from synthorg.tools.sandbox._memory_limit import parse_memory_limit

# A bind spec whose source is an absolute path (or drive letter) is a
# HOST bind-mount to Docker; only a named volume may reach Binds.
_VOLUME_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\A[a-zA-Z0-9][a-zA-Z0-9_.-]{0,254}\Z"
)

# All GPUs visible to the daemon; per-GPU selection is a deployment
# concern (NVIDIA_VISIBLE_DEVICES on the daemon / compose level).
_GPU_COUNT_ALL: Final[int] = -1
_PIDS_LIMIT: Final[int] = 256
_PROBE_MEMORY_LIMIT: Final[str] = "4g"
# /tmp stays exec-capable: torch JIT/inductor dlopens compiled
# artefacts, so noexec here would break CUDA kernel compilation.
_TMPFS_OPTIONS: Final[str] = "rw,nosuid,nodev,size=1g"
# ReadonlyRootfs: hub/torch caches must land on writable storage.
STAGE_CACHE_DIR: Final[str] = "/data/fine-tune/cache"
PROBE_CACHE_DIR: Final[str] = "/tmp"  # noqa: S108 -- container tmpfs, not host /tmp


def cache_env(cache_root: str) -> list[str]:
    """Cache-redirect env vars for a read-only-rootfs container.

    Returns:
        ``HF_HOME`` / ``TORCH_HOME`` / ``XDG_CACHE_HOME`` entries under
        *cache_root* (stage containers use the writable ``/data`` bind;
        the probe uses its ``/tmp`` tmpfs).
    """
    return [
        f"HF_HOME={cache_root}/hf",
        f"TORCH_HOME={cache_root}/torch",
        f"XDG_CACHE_HOME={cache_root}/xdg",
    ]


def _hardening_host_config() -> dict[str, object]:
    """Baseline hardening shared by stage and probe containers.

    Returns:
        Result of type ``dict[str, object]``.
    """
    return {
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges"],
        "ReadonlyRootfs": True,
        "Tmpfs": {"/tmp": _TMPFS_OPTIONS},  # noqa: S108 -- container path, not host
        "PidsLimit": _PIDS_LIMIT,
    }


def build_stage_host_config(
    execution: FineTuneExecutionConfig,
    data_volume: str,
) -> dict[str, object]:
    """Build the hardened HostConfig for a stage container.

    Returns:
        Result of type ``dict[str, object]``.

    Raises:
        FineTuneStageExecutionError: When *data_volume* is not a plain
            Docker volume name, or the memory limit fails to parse.
    """
    if _VOLUME_NAME_PATTERN.fullmatch(data_volume) is None:
        msg = (
            "fine-tune data volume must be a Docker volume name"
            f" (a path would become a host bind-mount): {data_volume!r}"
        )
        raise FineTuneStageExecutionError(msg)
    try:
        memory_bytes = parse_memory_limit(execution.memory_limit)
    except ValueError as exc:
        msg = (
            f"invalid fine-tune memory limit {execution.memory_limit!r}:"
            f" {safe_error_description(exc)}"
        )
        raise FineTuneStageExecutionError(msg) from exc
    host_config: dict[str, object] = {
        **_hardening_host_config(),
        "Binds": [f"{data_volume}:/data:rw"],
        "Memory": memory_bytes,
    }
    if execution.gpu_enabled:
        host_config["DeviceRequests"] = _gpu_device_requests()
    return host_config


def build_probe_host_config(*, gpu_enabled: bool) -> dict[str, object]:
    """Build the hardened HostConfig for a probe container.

    Returns:
        Result of type ``dict[str, object]``.
    """
    host_config: dict[str, object] = {
        **_hardening_host_config(),
        "Memory": parse_memory_limit(_PROBE_MEMORY_LIMIT),
    }
    if gpu_enabled:
        host_config["DeviceRequests"] = _gpu_device_requests()
    return host_config


def _gpu_device_requests() -> list[dict[str, object]]:
    """Docker DeviceRequests granting all NVIDIA GPUs.

    Returns:
        Result of type ``list[dict[str, object]]``.
    """
    return [
        {
            "Driver": "nvidia",
            "Count": _GPU_COUNT_ALL,
            "Capabilities": [["gpu"]],
        }
    ]
