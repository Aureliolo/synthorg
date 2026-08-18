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

import re
import shutil
import subprocess
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

#: The first dotted number in a ``--version`` line. Anchored on nothing else
#: because the surrounding wording differs per tool ("git version 2.48.1",
#: "pg_dump (PostgreSQL) 17.2").
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"(\d+(?:\.\d+)*)")

#: A version probe runs on the boot path, so it is bounded. A binary that
#: cannot answer this fast is treated as unreadable rather than as old.
_VERSION_PROBE_TIMEOUT_SECONDS: Final[float] = 5.0


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
        min_version: The oldest release that carries the behaviour this
            product depends on, or ``None`` when any version serves. Present
            because a binary can satisfy PATH and still not do the job: git
            IGNORES a config key it does not know rather than refusing it, so
            an old one accepts every option this product sets, reports
            nothing, and produces the broken result anyway.
        version_reason: What breaks below ``min_version``, in operator terms.
            Required whenever ``min_version`` is set, for the same reason
            ``consumers`` is required: it is rendered into the refusal.
    """

    name: str
    package: str
    consumers: tuple[str, ...]
    backend: PersistenceBackendKind | None = None
    min_version: tuple[int, ...] | None = None
    version_reason: str = ""

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
        if self.min_version is not None and not self.version_reason.strip():
            msg = "BinaryRecord.version_reason is required alongside min_version"
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
        # ``worktree.useRelativePaths`` landed in 2.48. A worktree created
        # without it records the absolute path the BACKEND saw, and the agent
        # opens that worktree through a different mount, so every git command
        # it runs fails with a "not a git repository" naming a path that
        # exists on one side of the mount only. Asserted at boot because git
        # ignores an unknown config key silently: an old binary accepts the
        # option, says nothing, and hands back the broken worktree, so the
        # first report is a failing agent deep inside a sandbox.
        min_version=(2, 48),
        version_reason=(
            "agent worktrees need 'worktree.useRelativePaths' (git 2.48), "
            "without which every git command an agent runs inside one fails"
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


def installed_version(name: str) -> tuple[int, ...] | None:
    """Read a binary's version by asking it, or ``None`` when it will not say.

    ``None`` covers every way the answer can fail to arrive: the call errors,
    times out, or prints something with no version in it. Each is treated the
    same by :func:`_too_old`, and deliberately does NOT refuse the boot: not
    knowing the version is not evidence of an old one, and refusing on a
    parse failure would take a working deployment down over output this
    parser did not anticipate.

    Returns:
        The leading numeric components, or ``None`` when unreadable.
    """
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            [name, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    match = _VERSION_RE.search(result.stdout or "")
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _too_old(record: BinaryRecord) -> tuple[int, ...] | None:
    """Return the installed version when it is below the record's floor.

    Returns:
        The offending version, or ``None`` when the record sets no floor, the
        version cannot be read, or it satisfies the floor.
    """
    if record.min_version is None:
        return None
    found = installed_version(record.name)
    if found is None:
        logger.warning(
            API_APP_STARTUP,
            service="binary_preflight",
            note="could not read binary version; version floor unverified",
            binary=record.name,
            min_version=".".join(str(part) for part in record.min_version),
        )
        return None
    # Compared over the shared prefix, so a floor of (2, 48) is satisfied by
    # 2.48.1 and by a four-component build such as 2.55.0.windows.3.
    width = len(record.min_version)
    return found if found[:width] < record.min_version else None


def _describe_old(record: BinaryRecord, found: tuple[int, ...]) -> str:
    """Render one too-old binary as an actionable sentence.

    Returns:
        A line naming the binary, the versions, and what breaks.
    """
    return (
        f"{record.name!r} is version {'.'.join(str(p) for p in found)}, below the "
        f"{'.'.join(str(p) for p in record.min_version or ())} this product "
        f"requires: {record.version_reason}; upgrade the "
        f"{record.package!r} package"
    )


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
    required = required_binaries_for(backend_name)
    missing = _absent(required)
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
    # Checked after presence, because a version probe needs the binary to
    # exist and "absent" is the more actionable message when it does not.
    outdated = [
        (record, found)
        for record in required
        if (found := _too_old(record)) is not None
    ]
    if outdated:
        detail = "; ".join(_describe_old(record, found) for record, found in outdated)
        logger.error(
            API_APP_STARTUP,
            service="binary_preflight",
            note="required binary too old; refusing to boot",
            binaries=[record.name for record, _ in outdated],
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
