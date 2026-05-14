"""Tests for file system tool helpers."""

import pytest

from synthorg.tools.file_system._base_fs_tool import _map_os_error


@pytest.mark.unit
@pytest.mark.parametrize(
    ("exc", "expected_key", "expected_msg_fragment"),
    [
        (
            FileNotFoundError("gone"),
            "not_found",
            "File not found: /workspace/missing.txt",
        ),
        (
            IsADirectoryError("is dir"),
            "is_directory",
            "Path is a directory, not a file: /workspace/missing.txt",
        ),
        (
            PermissionError("denied"),
            "permission_denied",
            "Permission denied: /workspace/missing.txt",
        ),
        (
            OSError("disk full"),
            "os_error",
            # ``safe_error_description`` prefixes the type name; the
            # surrounding format string supplies the verb + path.
            "OS error reading file '/workspace/missing.txt': OSError: disk full",
        ),
    ],
    ids=["file_not_found", "is_directory", "permission_denied", "generic_os"],
)
def test_map_os_error(
    exc: OSError,
    expected_key: str,
    expected_msg_fragment: str,
) -> None:
    key, msg = _map_os_error(exc, "/workspace/missing.txt", "reading")
    assert key == expected_key
    assert msg == expected_msg_fragment


@pytest.mark.unit
def test_map_os_error_dedicated_tool_hint_appended_for_directory() -> None:
    """Directory exceptions append the hint when supplied."""
    key, msg = _map_os_error(
        IsADirectoryError("is dir"),
        "/workspace/subdir",
        "deleting",
        dedicated_tool_hint="use a dedicated tool for directory removal",
    )
    assert key == "is_directory"
    assert msg == (
        "Path is a directory, not a file: /workspace/subdir"
        " (use a dedicated tool for directory removal)"
    )


@pytest.mark.unit
def test_map_os_error_dedicated_tool_hint_ignored_for_non_directory() -> None:
    """The hint is ignored unless the exception is IsADirectoryError."""
    key, msg = _map_os_error(
        FileNotFoundError("gone"),
        "/workspace/missing.txt",
        "deleting",
        dedicated_tool_hint="use a dedicated tool for directory removal",
    )
    assert key == "not_found"
    assert msg == "File not found: /workspace/missing.txt"
