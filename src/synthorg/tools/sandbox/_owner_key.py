"""Deciding which container a sandbox command may reuse.

Every segment of a lifecycle owner key answers one question: two commands
share a container exactly when everything the container was created with
is the same for both. Project, environment image and workspace mount mode
are all fixed at creation while the command that needs them arrives later,
so each is written into the key rather than checked afterwards.

Pure and container-free on purpose: the decision is about identity, and
nothing here talks to Docker.
"""

import hashlib
import re
import uuid
from typing import Final

import structlog.contextvars

from synthorg.observability import get_logger
from synthorg.observability.events.sandbox import (
    SANDBOX_LIFECYCLE_DISPATCH,
    SANDBOX_LIFECYCLE_OWNER_DEGRADED,
)
from synthorg.tools.sandbox._mount_mode import MountMode
from synthorg.tools.sandbox.lifecycle.config import (
    STRATEGY_PER_AGENT,
    STRATEGY_PER_TASK,
)

logger = get_logger(__name__)

# A reusable lifecycle owner must look like an agent/task identifier:
# a bounded slug (alnum, dash, underscore, colon, dot).  Anything else
# coming through the correlation context or an explicit caller is
# rejected so it cannot become a malformed Docker label or a poisoned
# reuse key; the call degrades to ephemeral per-call instead.
_OWNER_ID_MAX_LEN: Final[int] = 128
_OWNER_ID_RE: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")
# Truncated SHA-256 length for the environment-image segment of a reuse
# key: 12 hex chars (48 bits) make accidental cross-image collisions
# negligible while keeping the owner key well under the 128-char cap.
_IMAGE_SEGMENT_HASH_LEN: Final[int] = 12


def ephemeral_key() -> str:
    """Return a unique per-call owner key (no reuse).

    Returns:
        Result of type ``str``.
    """
    return f"per-call:{uuid.uuid4()}"


def valid_owner(key: str) -> bool:
    """Return whether *key* is a safe reuse / Docker-label owner id.

    Returns:
        ``True`` if the operation succeeds, ``False`` otherwise.
    """
    return len(key) <= _OWNER_ID_MAX_LEN and _OWNER_ID_RE.match(key) is not None


def context_owner(strategy_kind: str) -> str | None:
    """Return the owner id from the structlog correlation context, if any.

    Args:
        strategy_kind: The configured lifecycle strategy, which decides
            which correlation key names the owner.

    Returns:
        The resulting ``str``, or ``None`` when unavailable.
    """
    ctx = structlog.contextvars.get_contextvars()
    if strategy_kind == STRATEGY_PER_AGENT:
        ctx_key = ctx.get("agent_id")
    elif strategy_kind == STRATEGY_PER_TASK:
        ctx_key = ctx.get("task_id")
    else:
        ctx_key = None
    return str(ctx_key) if ctx_key else None


def context_project() -> str | None:
    """Return the project id from the structlog correlation context, if any.

    Returns:
        The resulting ``str``, or ``None`` when unavailable.
    """
    ctx = structlog.contextvars.get_contextvars()
    value = ctx.get("project_id")
    return str(value) if value else None


def project_prefixed(
    key: str,
    project_id: str | None,
    image_override: str | None = None,
    mount_mode: MountMode | None = None,
) -> str:
    """Prefix a reusable owner key with project + environment identity.

    Forces a per-agent/per-task reused container to be torn down and
    recreated when the project changes, so a container mounted for
    project A is never reused for project B (the isolation guarantee).
    ``None`` leaves the key unprefixed.

    When *image_override* is set (a per-project reproducible environment
    image is active), a short hash of it is appended so a warm container
    built under one declared image is never reused for a run that requires
    a different image; the new image would otherwise be silently ignored.
    ``None`` (no active environment) appends nothing, preserving the prior
    key shape.

    *mount_mode* is appended for the same reason and is load-bearing in
    the same way. A mount's writability is fixed when the container is
    created, while the category that decides it arrives per command, so
    without this segment the first command an owner runs would pin the
    mode for every later one: an agent that read a file before it built
    anything would find its workspace read-only for the rest of its life,
    which is exactly how a build stage reports a read-only filesystem on a
    workspace the design calls writable.

    Args:
        key: The unqualified owner id.
        project_id: Owning project, or ``None`` to leave it unprefixed.
        image_override: Active reproducible-environment image, or ``None``.
        mount_mode: Workspace mount mode, or ``None`` to leave the key
            unqualified by it.

    Returns:
        Result of type ``str``.
    """
    prefixed = f"{project_id}:{key}" if project_id else key
    if image_override:
        digest = hashlib.sha256(image_override.encode("utf-8")).hexdigest()
        prefixed = f"{prefixed}:img-{digest[:_IMAGE_SEGMENT_HASH_LEN]}"
    if mount_mode:
        prefixed = f"{prefixed}:{mount_mode}"
    return prefixed


def _degrade(
    strategy_kind: str, *, owner_source: str | None, reason: str
) -> tuple[str, bool]:
    """Log a key that cannot be reused and return the ephemeral fallback.

    Args:
        strategy_kind: The configured lifecycle strategy, for the log.
        owner_source: Where the rejected owner came from, or ``None`` when
            no owner was derivable at all.
        reason: Why the key cannot be reused.

    Returns:
        ``(ephemeral_key, False)``, the caller's degraded result.
    """
    fields = {"strategy": strategy_kind, "reason": reason}
    if owner_source is not None:
        fields["owner_source"] = owner_source
    logger.warning(SANDBOX_LIFECYCLE_OWNER_DEGRADED, **fields)
    return ephemeral_key(), False


def resolve_lifecycle(
    owner_id: str | None,
    *,
    strategy_kind: str,
    reuses_container: bool,
    project_id: str | None = None,
    image_override: str | None = None,
    mount_mode: MountMode | None = None,
) -> tuple[str, bool]:
    """Resolve the lifecycle owner key and teardown ownership.

    An explicit *owner_id* wins; otherwise, for a reuse strategy the key
    is derived from the structlog correlation context (``agent_id`` for
    per-agent, ``task_id`` for per-task). A per-call strategy, an
    underivable owner, or a malformed owner all degrade to ephemeral
    per-call (``strategy_owns`` ``False`` so the backend destroys the
    container and the strategy is not poisoned).

    A reusable key is prefixed with ``<project_id>:`` so a container
    mounted for one project is never reused for another, and suffixed with
    the active environment image identity so a container built under one
    declared image is never reused for a different one, and with the
    workspace mount mode so a container mounted read-only is never reused
    for a command entitled to write.

    Args:
        owner_id: Explicit lifecycle owner, or ``None``.
        strategy_kind: The configured lifecycle strategy's name.
        reuses_container: Whether that strategy reuses containers at all.
        project_id: Owning project, or ``None`` for the no-project
            execution mode.
        image_override: Active reproducible-environment image, or ``None``
            when no per-project environment is active.
        mount_mode: Workspace mount mode this command needs, or ``None``
            to leave the key unqualified by it.

    Returns:
        ``(owner_key, strategy_owns_teardown)``.
    """
    if owner_id is not None and owner_id.strip():
        key = owner_id.strip()
        if not valid_owner(key):
            return _degrade(
                strategy_kind,
                owner_source="explicit",
                reason="owner_id failed format validation",
            )
        prefixed = project_prefixed(key, project_id, image_override, mount_mode)
        if not valid_owner(prefixed):
            return _degrade(
                strategy_kind,
                owner_source="explicit",
                reason="project-prefixed owner_id failed format validation",
            )
        # DEBUG, not INFO: this fires once per sandboxed command, and the
        # routine outcome carries no decision a reader needs. Its neighbour
        # one branch down is the one worth a level, because a DEGRADED owner
        # means containers stopped being reused.
        logger.debug(
            SANDBOX_LIFECYCLE_DISPATCH,
            strategy=strategy_kind,
            owner_id=prefixed,
            owner_source="explicit",
            strategy_owns=reuses_container,
        )
        return prefixed, reuses_container

    if not reuses_container:
        return ephemeral_key(), False

    ctx_key = context_owner(strategy_kind)
    if ctx_key is not None and valid_owner(ctx_key):
        prefixed = project_prefixed(ctx_key, project_id, image_override, mount_mode)
        if not valid_owner(prefixed):
            return _degrade(
                strategy_kind,
                owner_source="correlation_context",
                reason="project-prefixed owner_id failed format validation",
            )
        logger.debug(
            SANDBOX_LIFECYCLE_DISPATCH,
            strategy=strategy_kind,
            owner_id=prefixed,
            owner_source="correlation_context",
            strategy_owns=True,
        )
        return prefixed, True

    return _degrade(
        strategy_kind,
        owner_source=None,
        reason=(
            "no valid explicit owner_id and no usable correlation "
            "context; container will not be reused (ephemeral per-call)"
        ),
    )
