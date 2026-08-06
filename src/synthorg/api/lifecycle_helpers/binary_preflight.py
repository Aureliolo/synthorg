# module-kind: code
"""Startup assertion that every binary the backend shells out to is present.

The backend spawns a handful of external programs, and a shipped image
that omits one fails only at the moment the feature is used: workspace
provisioning is on the critical path of every dispatch, so an image
without ``git`` cannot execute a single task while every test passes on a
developer machine where ``git`` is on PATH.

Two tiers, because the tree already treats them differently and one rule
would contradict the other. A REQUIRED binary is one whose absence makes
the product unable to do its job, so the boot is refused with an error
naming the binary, the package that provides it, and what it breaks: the
operator's fix is an image rebuild, and there is nothing to gain from
booting into a backend that cannot dispatch. An OPTIONAL binary already
guards its own use with a PATH lookup and degrades cleanly, so its
absence is reported as the reason its subsystems are blocked instead.

The manifest is the single place the two lists are stated, so
``docker/backend/apko.yaml`` and this file cannot drift apart without the
boot saying so.
"""

import shutil
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)

#: The backend that needs the PostgreSQL client tools. A SQLite deployment
#: never shells out to them, so demanding them there would refuse a boot
#: over a capability that deployment does not have.
_POSTGRES_BACKEND: Final[str] = "postgres"


class RequiredBinaryMissingError(DomainError):
    """A binary the backend cannot work without is not on PATH."""

    default_message: ClassVar[str] = "A required external binary is missing"
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR


class BinaryRequirement(StrEnum):
    """How badly the backend needs a binary.

    Attributes:
        REQUIRED: Its absence makes the product unable to do its job, so
            the boot is refused rather than deferred to first use.
        OPTIONAL: Its consumer guards the lookup and degrades, so the
            absence blocks that subsystem and nothing else.
    """

    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class BinaryRecord:
    """One external program the backend spawns.

    Attributes:
        name: The program as it is spawned, resolved through PATH.
        requirement: Whether its absence refuses the boot.
        package: The image package providing it, so the operator's fix is
            in the message rather than in a maintainer's head.
        consumers: What stops working without it, in operator terms.
        backend: The persistence backend that needs it, or ``None`` when
            every deployment does.
    """

    name: str
    requirement: BinaryRequirement
    package: str
    consumers: tuple[str, ...]
    backend: str | None = None


#: Every program the backend spawns. Derived from the subprocess call sites:
#: ``engine/workspace/_git_subprocess.py`` (git, eleven consumers),
#: ``persistence/postgres/pg_subprocess.py`` (the backup handlers), and the
#: two tunnel adapters. Deliberately no ``docker``: the devcontainer image
#: build goes through ``aiodocker`` over the mounted socket. Deliberately no
#: shell either: nothing under ``src/synthorg/`` calls
#: ``create_subprocess_shell``, so the distroless image having no shell is
#: not a defect.
BINARY_MANIFEST: Final[tuple[BinaryRecord, ...]] = (
    BinaryRecord(
        name="git",
        requirement=BinaryRequirement.REQUIRED,
        package="git",
        consumers=(
            "workspace provisioning (every dispatch)",
            "the git backends",
            "the docs engine",
            "the project brain",
            "the agent git tools",
        ),
    ),
    BinaryRecord(
        name="pg_dump",
        requirement=BinaryRequirement.REQUIRED,
        package="postgresql-client",
        consumers=("the Postgres backup handler",),
        backend=_POSTGRES_BACKEND,
    ),
    BinaryRecord(
        name="pg_restore",
        requirement=BinaryRequirement.REQUIRED,
        package="postgresql-client",
        consumers=("the Postgres restore handler",),
        backend=_POSTGRES_BACKEND,
    ),
    BinaryRecord(
        name="cloudflared",
        requirement=BinaryRequirement.OPTIONAL,
        package="cloudflared",
        consumers=("the Cloudflare tunnel adapter",),
    ),
    BinaryRecord(
        name="devtunnel",
        requirement=BinaryRequirement.OPTIONAL,
        package="devtunnel",
        consumers=("the dev-tunnels adapter",),
    ),
)


def _applies(record: BinaryRecord, backend_name: str) -> bool:
    """Whether *record* is needed by a deployment on *backend_name*.

    Returns:
        ``True`` when the record is backend-independent or names this one.
    """
    return record.backend is None or record.backend == backend_name


def required_binaries_for(backend_name: str) -> tuple[BinaryRecord, ...]:
    """Return the binaries a *backend_name* deployment cannot boot without.

    Args:
        backend_name: The configured persistence backend.

    Returns:
        The applicable REQUIRED records, in manifest order.
    """
    return tuple(
        record
        for record in BINARY_MANIFEST
        if record.requirement is BinaryRequirement.REQUIRED
        and _applies(record, backend_name)
    )


def _optional_binaries_for(backend_name: str) -> tuple[BinaryRecord, ...]:
    """Return the applicable OPTIONAL records, in manifest order."""
    return tuple(
        record
        for record in BINARY_MANIFEST
        if record.requirement is BinaryRequirement.OPTIONAL
        and _applies(record, backend_name)
    )


def _absent(records: tuple[BinaryRecord, ...]) -> tuple[BinaryRecord, ...]:
    """Return the records whose binary does not resolve on PATH."""
    return tuple(record for record in records if shutil.which(record.name) is None)


def _describe(record: BinaryRecord) -> str:
    """Render one missing binary as an actionable sentence.

    Returns:
        A line naming the binary, what it breaks, and the package that
        supplies it.
    """
    return (
        f"{record.name!r} is not on PATH, which breaks "
        f"{', '.join(record.consumers)}; install the {record.package!r} package"
    )


def run_binary_preflight(*, backend_name: str) -> tuple[BinaryRecord, ...]:
    """Assert the required binaries are present; report the optional ones.

    Args:
        backend_name: The configured persistence backend, which decides
            whether the PostgreSQL client tools are required.

    Returns:
        The absent OPTIONAL records, for the caller to record as blocked
        reasons.

    Raises:
        RequiredBinaryMissingError: When any required binary is absent.
            Raised rather than logged: the product cannot dispatch
            without it, and an image that ships without one is a build
            defect the operator fixes by rebuilding, not by restarting.
    """
    missing_required = _absent(required_binaries_for(backend_name))
    if missing_required:
        detail = "; ".join(_describe(record) for record in missing_required)
        logger.error(
            API_APP_STARTUP,
            service="binary_preflight",
            note="required binary missing; refusing to boot",
            binaries=[record.name for record in missing_required],
            backend_name=backend_name,
        )
        raise RequiredBinaryMissingError(detail)

    missing_optional = _absent(_optional_binaries_for(backend_name))
    for record in missing_optional:
        logger.warning(
            API_APP_STARTUP,
            service="binary_preflight",
            note="optional binary missing; its subsystems stay blocked",
            binary=record.name,
            detail=_describe(record),
        )
    logger.info(
        API_APP_STARTUP,
        service="binary_preflight",
        note="required binaries present",
        backend_name=backend_name,
        optional_missing=len(missing_optional),
    )
    return missing_optional


__all__ = [
    "BINARY_MANIFEST",
    "BinaryRecord",
    "BinaryRequirement",
    "RequiredBinaryMissingError",
    "required_binaries_for",
    "run_binary_preflight",
]
