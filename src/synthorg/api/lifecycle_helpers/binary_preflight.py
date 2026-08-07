# module-kind: code
"""Startup assertion that the binaries the backend cannot supply are present.

The backend spawns a handful of external programs, and a shipped image
that omits one fails only at the moment the feature is used: workspace
provisioning is on the critical path of every dispatch, so an image
without ``git`` cannot execute a single task while every test passes on a
developer machine where ``git`` is on PATH.

The manifest holds the programs the backend needs and cannot obtain for
itself. Their absence makes the product unable to do its job, so the boot
is refused with an error naming the binary, the package that provides it,
and what it breaks: the operator's fix is an image rebuild, and there is
nothing to gain from booting into a backend that cannot dispatch.

A program the backend provisions at runtime is deliberately NOT here. The
Cloudflare and Dev Tunnels adapters download their vendor CLI on first
start, and each already answers ``availability()`` with the live state
including whether that download is switched off, so a PATH check at boot
would report a fetchable binary as missing and duplicate a better report.
The same reasoning excludes ``nix``, which provisions inside the sandbox.

The manifest is the single place the list is stated, so
``docker/backend/apko.yaml`` and this file cannot drift apart without the
boot saying so.
"""

import shutil
from dataclasses import dataclass
from typing import ClassVar, Final

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackendKind

logger = get_logger(__name__)

#: The backend that needs the PostgreSQL client tools. A SQLite deployment
#: never shells out to them, so demanding them there would refuse a boot
#: over a capability that deployment does not have. Taken from the backend
#: discriminator rather than spelled here: a typoed literal would match no
#: deployment, and a manifest entry that matches nothing is a preflight that
#: silently checks nothing.
_POSTGRES_BACKEND: Final[PersistenceBackendKind] = PersistenceBackendKind.POSTGRES


class RequiredBinaryMissingError(DomainError):
    """A binary the backend cannot work without is not on PATH."""

    default_message: ClassVar[str] = "A required external binary is missing"
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR


@dataclass(frozen=True, slots=True)
class BinaryRecord:
    """One external program the backend spawns and cannot supply itself.

    Attributes:
        name: The program as it is spawned, resolved through PATH.
        package: The image package providing it, so the operator's fix is
            in the message rather than in a maintainer's head.
        consumers: What stops working without it, in operator terms.
        backend: The persistence backend that needs it, or ``None`` when
            every deployment does. Typed as the discriminator so a record
            cannot name a backend that does not exist and quietly apply to
            no deployment at all.
    """

    name: str
    package: str
    consumers: tuple[str, ...]
    backend: PersistenceBackendKind | None = None

    def __post_init__(self) -> None:
        """Refuse a record that cannot produce an actionable message.

        Raises:
            ValueError: When the name, the package or the consumer list is
                empty. Each is rendered into the boot-refusal message an
                operator acts on, and a blank one turns that message into
                "'' is not on PATH, which breaks ; install the '' package".
        """
        for field, value in (
            ("name", self.name),
            ("package", self.package),
        ):
            if not value.strip():
                msg = f"BinaryRecord.{field} must not be blank"
                raise ValueError(msg)
        if not self.consumers or not all(item.strip() for item in self.consumers):
            msg = "BinaryRecord.consumers must name at least one non-blank consumer"
            raise ValueError(msg)


#: Every program the backend spawns and cannot obtain for itself. Derived
#: from the subprocess call sites: ``engine/workspace/_git_subprocess.py``
#: and ``tools/_git_subprocess.py`` (git, across the workspace, docs-engine,
#: project-brain and agent-tool paths), and
#: ``persistence/postgres/pg_subprocess.py`` (the backup handlers).
#: Deliberately no ``docker``: the devcontainer image build goes through
#: ``aiodocker`` over the mounted socket. Deliberately no shell either:
#: nothing under ``src/synthorg/`` calls ``create_subprocess_shell``, so the
#: distroless image having no shell is not a defect.
BINARY_MANIFEST: Final[tuple[BinaryRecord, ...]] = (
    BinaryRecord(
        name="git",
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
        package="postgresql-client",
        consumers=("the Postgres backup handler",),
        backend=_POSTGRES_BACKEND,
    ),
    BinaryRecord(
        name="pg_restore",
        package="postgresql-client",
        consumers=("the Postgres restore handler",),
        backend=_POSTGRES_BACKEND,
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
        The applicable records, in manifest order.
    """
    return tuple(record for record in BINARY_MANIFEST if _applies(record, backend_name))


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


def run_binary_preflight(*, backend_name: str) -> None:
    """Assert every binary this deployment cannot supply for itself is present.

    Args:
        backend_name: The configured persistence backend, which decides
            whether the PostgreSQL client tools are required.

    Raises:
        RequiredBinaryMissingError: When any of them is absent. Raised
            rather than logged: the product cannot dispatch without it,
            and an image that ships without one is a build defect the
            operator fixes by rebuilding, not by restarting.
    """
    missing = _absent(required_binaries_for(backend_name))
    if missing:
        detail = "; ".join(_describe(record) for record in missing)
        logger.error(
            API_APP_STARTUP,
            service="binary_preflight",
            note="required binary missing; refusing to boot",
            binaries=[record.name for record in missing],
            backend_name=backend_name,
        )
        raise RequiredBinaryMissingError(detail)
    logger.info(
        API_APP_STARTUP,
        service="binary_preflight",
        note="required binaries present",
        backend_name=backend_name,
    )


__all__ = [
    "BINARY_MANIFEST",
    "BinaryRecord",
    "RequiredBinaryMissingError",
    "required_binaries_for",
    "run_binary_preflight",
]
