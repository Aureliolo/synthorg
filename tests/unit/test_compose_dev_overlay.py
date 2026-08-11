"""The dev overlay differs from the shipped backend in exactly the right ways.

`docker/compose.dev.yml` runs the shipped image against a mounted worktree. Two
of its settings are load-bearing rather than cosmetic, and both fail silently if
they drift: without the bytecode redirect the container reads whatever
`__pycache__` a local `pytest` run left in the worktree (possibly
typeguard-instrumented), and without the read-only flag on the source mount a
container could write into a developer's checkout.

The deleted-script assertion is here for the same reason: a reference to a
script that no longer exists points a reader at a path that cannot answer, and
nothing else catches that class of drift.

`make dev-status` is asserted here too, because it is the one command that
answers whether the arm can execute an agent tool at all, and both ways it can
fail are silent: reading a guarded endpoint without a session, and printing a
verdict it does not act on.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OVERLAY = _REPO_ROOT / "docker" / "compose.dev.yml"
_MAKEFILE = _REPO_ROOT / "Makefile"
_DELETED_SCRIPTS = ("scripts/dev/run_api.py", "scripts/dev/backend_dev.mjs")
_SEARCHED_SUFFIXES = (".md", ".py", ".yml", ".yaml", ".mjs", ".toml", ".sh")


def _recipe(target: str) -> str:
    """Return one Makefile target's recipe.

    Args:
        target: Target name, without its colon.

    Returns:
        The tab-indented recipe body, newline-joined.
    """
    lines = _MAKEFILE.read_text(encoding="utf-8").splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.startswith(f"{target}:")
    )
    body: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        body.append(line)
    return "\n".join(body)


class _ComposeLoader(yaml.SafeLoader):
    """A safe loader that understands Compose's own merge tags.

    Compose reads `!override` on a sequence to mean "replace the base file's
    value rather than merge with it", which `safe_load` refuses as an unknown
    tag. Constructing it as the plain sequence it decorates keeps the parse
    safe while letting the assertions below see the value.
    """


def _construct_tagged(loader: yaml.SafeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    msg = f"unexpected node for a compose tag: {type(node).__name__}"
    raise TypeError(msg)


_ComposeLoader.add_constructor("!override", _construct_tagged)
_ComposeLoader.add_constructor("!reset", _construct_tagged)


@pytest.fixture(scope="module")
def overlay() -> dict[str, object]:
    parsed: dict[str, object] = yaml.load(
        _OVERLAY.read_text(encoding="utf-8"),
        Loader=_ComposeLoader,  # noqa: S506 -- a SafeLoader subclass, tags aside
    )
    return parsed


def _backend(overlay: dict[str, object]) -> dict[str, object]:
    services: dict[str, dict[str, object]] = overlay["services"]  # type: ignore[assignment]
    return services["backend"]


class TestSourceMount:
    def test_the_worktree_is_mounted_read_only(
        self, overlay: dict[str, object]
    ) -> None:
        mounts: list[str] = _backend(overlay)["volumes"]  # type: ignore[assignment]
        source = next(mount for mount in mounts if mount.endswith("/app/src:ro"))
        assert source.endswith(":ro")
        assert "/src:" in source

    def test_the_bytecode_cache_is_writable_storage(
        self, overlay: dict[str, object]
    ) -> None:
        # The shipped image mounts its root filesystem read-only, so the cache
        # has to land on a volume or nothing is cached and every restart
        # recompiles the tree.
        mounts: list[str] = _backend(overlay)["volumes"]  # type: ignore[assignment]
        assert any(mount.startswith("synthorg-devcache:") for mount in mounts)
        assert "synthorg-devcache" in overlay["volumes"]  # type: ignore[operator]


class TestBytecodeRedirect:
    def test_the_cache_prefix_points_at_that_volume(
        self, overlay: dict[str, object]
    ) -> None:
        env: dict[str, str] = _backend(overlay)["environment"]  # type: ignore[assignment]
        mounts: list[str] = _backend(overlay)["volumes"]  # type: ignore[assignment]
        prefix = env["PYTHONPYCACHEPREFIX"]
        assert any(mount.endswith(f":{prefix}") for mount in mounts)

    def test_bytecode_writing_is_re_enabled(self, overlay: dict[str, object]) -> None:
        # The image sets this to 1. CPython reads an EMPTY value as unset, which
        # is what lets the overlay turn it off without a second image.
        env: dict[str, str] = _backend(overlay)["environment"]  # type: ignore[assignment]
        assert env["PYTHONDONTWRITEBYTECODE"] == ""


class TestTheAuthBypassCannotLeaveTheMachine:
    def test_the_backend_is_published_on_loopback_only(
        self, overlay: dict[str, object]
    ) -> None:
        # This overlay turns on a password-free /auth/dev-login that mints a
        # real admin session for ONE unauthenticated request. The base file
        # publishes on 0.0.0.0, so without a loopback bind anything that can
        # route to the developer's machine can take that session. Being opt-in
        # bounds which deployments carry the bypass, never who can reach one.
        published: list[str] = _backend(overlay)["ports"]  # type: ignore[assignment]
        assert published, "the overlay must state its own publish, not inherit it"
        for entry in published:
            assert entry.startswith("127.0.0.1:"), (
                f"{entry!r} is reachable beyond this machine while the dev "
                "auth bypass is enabled"
            )

    def test_the_bypass_is_actually_the_thing_being_contained(
        self, overlay: dict[str, object]
    ) -> None:
        # Guards the pairing: if the bypass is ever removed the loopback bind
        # can relax, and if it is added elsewhere this test should be the one
        # that fails.
        env: dict[str, str] = _backend(overlay)["environment"]  # type: ignore[assignment]
        assert env["SYNTHORG_DEV_AUTH_BYPASS"] == "true"


class TestItStaysADevOverlay:
    def test_it_builds_from_the_worktree_rather_than_pulling(
        self, overlay: dict[str, object]
    ) -> None:
        build: dict[str, object] = _backend(overlay)["build"]  # type: ignore[assignment]
        assert build["dockerfile"] == "docker/backend/Dockerfile"
        assert "BASE_IMAGE" in build["args"]  # type: ignore[operator]

    def test_every_path_is_supplied_absolutely(
        self, overlay: dict[str, object]
    ) -> None:
        # Compose resolves a relative path against the FIRST compose file's
        # directory, which for this overlay is the operator's state directory
        # rather than the repository.
        backend = _backend(overlay)
        build: dict[str, object] = backend["build"]  # type: ignore[assignment]
        mounts: list[str] = backend["volumes"]  # type: ignore[assignment]
        assert str(build["context"]).startswith("${SYNTHORG_REPO_ROOT")
        source = next(mount for mount in mounts if mount.endswith("/app/src:ro"))
        assert source.startswith("${SYNTHORG_REPO_ROOT")


class TestTheCapabilityCheckAnswersOrFails:
    """`make dev-status` reports the agent-tool verdict, or exits non-zero.

    `/api/v1/subsystems` is behind `require_read_access`, so a read without a
    session is a 401. Swallowing that answered "read it from the dashboard" for
    every deployment, including the ones whose activation had declined, and
    still exited zero, which is the pair of failures this class pins.
    """

    def test_the_subsystem_read_carries_a_session(self) -> None:
        recipe = _recipe("dev-status")
        login = recipe.index("/api/v1/auth/dev-login")
        read = recipe.index("/api/v1/subsystems")
        assert login < read, "the session must be minted before the read needs it"
        assert '-b "$$jar"' in recipe, "the read must send the jar the login filled"

    def test_the_session_does_not_outlive_the_command(self) -> None:
        # It is a real admin session on a password-free endpoint, so the jar is
        # a credential; the trap removes it on every exit path, not just the
        # one where the phase reads back active.
        recipe = _recipe("dev-status")
        assert "trap 'rm -f \"$$jar\"' EXIT" in recipe

    def test_only_an_active_phase_passes(self) -> None:
        recipe = _recipe("dev-status")
        guard = 'test "$$phase" = active ||'
        assert guard in recipe
        assert "exit 1" in recipe[recipe.index(guard) :]

    def test_no_failure_branch_merely_prints(self) -> None:
        # `|| echo` is the shape the defect had: the read failed, a message
        # went out, and the target still exited zero, so `make dev-up`
        # reported an arm that could not run a single agent tool as ready.
        assert "|| echo" not in _recipe("dev-status")


class TestTheNativeArmIsGone:
    @pytest.mark.parametrize("script", _DELETED_SCRIPTS)
    def test_no_file_still_points_at_it(
        self, script: str, searchable: list[tuple[Path, str]]
    ) -> None:
        name = script.rsplit("/", maxsplit=1)[-1]
        offenders = [
            str(path.relative_to(_REPO_ROOT))
            for path, text in searchable
            if name in text
        ]
        assert offenders == [], f"{script} is deleted but still referenced"


@pytest.fixture(scope="module")
def searchable() -> list[tuple[Path, str]]:
    """Return every TRACKED repository text file and its contents, read once.

    Sourced from ``git ls-files`` rather than a directory walk with a skip
    list. What this asserts is about the repository, and untracked scratch
    output is not the repository: local audit and triage notes discuss the
    deleted scripts by name, and a walk would read them and fail the suite for
    something no contributor can see. Tracked-only is correct by construction
    and cannot regress when a new ignored directory appears.

    This module is excluded: it names the deleted scripts in order to assert
    that nothing else does.

    Returns:
        ``(path, text)`` pairs for tracked files with a searched suffix.
    """
    listed = subprocess.run(  # noqa: S603
        ["git", "-C", str(_REPO_ROOT), "ls-files", "-z"],  # noqa: S607
        capture_output=True,
        check=True,
    )
    self_path = Path(__file__).resolve()
    found: list[tuple[Path, str]] = []
    for entry in listed.stdout.decode("utf-8").split("\0"):
        if not entry.endswith(_SEARCHED_SUFFIXES):
            continue
        path = _REPO_ROOT / entry
        if not path.is_file() or path.resolve() == self_path:
            continue
        found.append((path, path.read_text(encoding="utf-8", errors="replace")))
    return found
