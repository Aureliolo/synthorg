"""Tests for per-category workspace mount mode."""

import typing

import pytest

from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.sandbox._mount_mode import (
    MOUNT_MODES,
    WRITABLE_WORKSPACE_CATEGORIES,
    MountMode,
    resolve_mount_mode,
)
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox_exec import DockerSandboxExecMixin

pytestmark = pytest.mark.unit


class TestOneDomainNotThree:
    """Every place that names a mount mode names the same domain.

    The mode was declared three times over: a ``Literal`` on the config, a
    tuple the teardown sweeps, and a bare constant the Mounts API is compared
    against. Nothing tied them, so a mode added to one was absent from the
    others, and the one that matters is the sweep: a container created under
    a mode ``release_owner`` does not name runs until the process exits.
    """

    def test_the_sweep_set_is_exactly_the_enum(self) -> None:
        assert set(MOUNT_MODES) == set(MountMode)

    def test_the_configured_field_admits_exactly_the_enum(self) -> None:
        """A third mode cannot reach the config without joining the sweep."""
        annotation = DockerSandboxConfig.model_fields["mount_mode"].annotation
        assert annotation is MountMode

    def test_the_resolver_returns_members_of_that_domain(self) -> None:
        resolved = resolve_mount_mode(
            ToolCategory.CODE_EXECUTION.value, MountMode.READ_ONLY
        )
        assert resolved is MountMode.READ_WRITE

    def test_a_member_still_reads_as_its_docker_spelling(self) -> None:
        """The value is interpolated straight into a bind string."""
        assert f"{MountMode.READ_WRITE}" == "rw"
        assert typing.cast(str, MountMode.READ_ONLY) == "ro"


@pytest.mark.parametrize(
    "category",
    [
        ToolCategory.BROWSER,
        ToolCategory.CODE_EXECUTION,
        ToolCategory.DESKTOP,
        ToolCategory.TERMINAL,
        ToolCategory.VERSION_CONTROL,
    ],
)
def test_a_category_whose_output_lands_in_the_workspace_can_write(
    category: ToolCategory,
) -> None:
    """A build writes objects, a shell writes output, git writes its own directory.

    Browser and desktop are here for the same reason and are easy to miss: a
    screenshot under ``/workspace/.synthorg/screenshots/`` IS the result of the
    check, so a read-only mount does not confine those tools, it breaks them.

    Read-only makes each of these tools decorative rather than confined.
    """
    resolved = resolve_mount_mode(category.value, MountMode.READ_ONLY)
    assert resolved is MountMode.READ_WRITE


@pytest.mark.parametrize(
    "category",
    [
        ToolCategory.WEB,
        ToolCategory.DATABASE,
        ToolCategory.MEMORY,
        ToolCategory.ANALYTICS,
    ],
)
def test_a_category_that_only_reads_keeps_the_configured_mode(
    category: ToolCategory,
) -> None:
    """Nothing is widened for a tool with no reason to change the project."""
    resolved = resolve_mount_mode(category.value, MountMode.READ_ONLY)
    assert resolved is MountMode.READ_ONLY


def test_an_absent_category_keeps_the_configured_mode() -> None:
    """The default answers a container built without a category.

    Falling open here would grant write to whatever forgot to say what it was.
    """
    assert resolve_mount_mode("", MountMode.READ_ONLY) is MountMode.READ_ONLY


def test_an_operator_read_write_default_is_honoured_for_every_category() -> None:
    """The resolver only ever widens; it never overrides a broader default."""
    resolved = resolve_mount_mode(ToolCategory.WEB.value, MountMode.READ_WRITE)
    assert resolved is MountMode.READ_WRITE


def test_the_writable_set_is_named_by_category_value() -> None:
    """The set is compared against what the sandbox is handed, a category value.

    Holding enum members here would silently match nothing, since the caller
    passes ``ToolCategory.X.value``.
    """
    assert all(isinstance(entry, str) for entry in WRITABLE_WORKSPACE_CATEGORIES)
    assert ToolCategory.CODE_EXECUTION.value in WRITABLE_WORKSPACE_CATEGORIES


def test_every_resolvable_mode_is_in_the_sweep_set() -> None:
    """``release_owner`` tears down one container per mode, so it must know them all.

    A mode the resolver can return but the sweep does not name leaves that
    container running until process shutdown.
    """
    resolvable = {
        resolve_mount_mode(category, configured)
        for category in (ToolCategory.CODE_EXECUTION.value, ToolCategory.WEB.value, "")
        for configured in MountMode
    }
    assert resolvable <= set(MOUNT_MODES)


class TestLifecycleKeyIsQualifiedByMountMode:
    """A container's writability is fixed at creation; its category is not.

    The defect this pins: with one key per owner, the first command an agent
    ran decided the mount mode for every later one, so an agent that read a
    file before it built anything spent the rest of its life on a read-only
    workspace and its build reported a read-only filesystem.
    """

    def test_two_modes_produce_two_keys(self) -> None:
        build = DockerSandboxExecMixin._project_prefixed(
            "agent", "proj", None, MountMode.READ_WRITE
        )
        browse = DockerSandboxExecMixin._project_prefixed(
            "agent", "proj", None, MountMode.READ_ONLY
        )
        assert build != browse

    def test_the_mode_survives_an_environment_image(self) -> None:
        """Both suffixes are appended, not one replacing the other."""
        key = DockerSandboxExecMixin._project_prefixed(
            "agent", "proj", "example-registry/img:1", MountMode.READ_WRITE
        )
        assert key.startswith("proj:agent:img-")
        assert key.endswith(":rw")

    def test_an_absent_mode_leaves_the_key_shape_unchanged(self) -> None:
        """Callers that do not run commands keep the key they always had."""
        assert (
            DockerSandboxExecMixin._project_prefixed("agent", "proj", None, None)
            == "proj:agent"
        )
