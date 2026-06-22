"""Unit tests for scope validator."""

import pytest

from synthorg.meta.models import CodeChange, CodeOperation
from synthorg.meta.validation.scope_validator import ScopeValidator

pytestmark = pytest.mark.unit

_ALLOWED = (
    "src/synthorg/meta/strategies/*",
    "src/synthorg/meta/guards/*",
    "src/synthorg/meta/rules/*",
)

_FORBIDDEN = (
    "src/synthorg/core/security/*",
    "src/synthorg/auth/*",
)


def _validator(
    *,
    allowed: tuple[str, ...] = _ALLOWED,
    forbidden: tuple[str, ...] = _FORBIDDEN,
) -> ScopeValidator:
    return ScopeValidator(allowed_paths=allowed, forbidden_paths=forbidden)


def _change(file_path: str) -> CodeChange:
    return CodeChange(
        file_path=file_path,
        operation=CodeOperation.CREATE,
        new_content="content",
        description="test",
        reasoning="test",
    )


class TestIsPathAllowed:
    """Path allowlist/denylist tests."""

    @pytest.mark.parametrize(
        "path",
        [
            "src/synthorg/meta/strategies/new_algo.py",
            "src/synthorg/meta/guards/custom_guard.py",
            "src/synthorg/meta/rules/custom_rule.py",
        ],
    )
    def test_allowed_paths_pass(self, path: str) -> None:
        assert _validator().is_path_allowed(path)

    @pytest.mark.parametrize(
        "path",
        [
            "src/synthorg/core/security/auth_handler.py",
            "src/synthorg/auth/tokens.py",
        ],
    )
    def test_forbidden_paths_rejected(self, path: str) -> None:
        assert not _validator().is_path_allowed(path)

    def test_path_outside_allowed_rejected(self) -> None:
        assert not _validator().is_path_allowed("src/synthorg/engine/core.py")

    def test_forbidden_overrides_allowed(self) -> None:
        v = ScopeValidator(
            allowed_paths=("src/synthorg/*",),
            forbidden_paths=("src/synthorg/auth/*",),
        )
        assert not v.is_path_allowed("src/synthorg/auth/login.py")
        assert v.is_path_allowed("src/synthorg/meta/strategies/x.py")

    def test_backslash_normalized(self) -> None:
        assert _validator().is_path_allowed("src\\synthorg\\meta\\strategies\\new.py")

    @pytest.mark.parametrize(
        "path",
        [
            "C:\\Windows\\System32\\drivers\\etc\\hosts",
            "C:/Windows/System32/config",
            "d:\\secrets\\key.py",
            "\\\\server\\share\\payload.py",
            "//server/share/payload.py",
        ],
    )
    def test_windows_absolute_and_unc_paths_rejected(self, path: str) -> None:
        """Drive-absolute / UNC paths must be rejected.

        After backslash normalisation ``PurePosixPath`` does not flag a
        Windows drive root absolute, so the guard checks the drive-letter /
        UNC roots explicitly to close the traversal gap on Windows hosts.
        """
        v = ScopeValidator(allowed_paths=("**/*", "*"), forbidden_paths=())
        assert not v.is_path_allowed(path)

    def test_empty_allowed_rejects_all(self) -> None:
        v = ScopeValidator(allowed_paths=(), forbidden_paths=())
        assert not v.is_path_allowed("src/any/path.py")


class TestValidateChanges:
    """Batch change validation tests."""

    def test_all_valid_returns_empty(self) -> None:
        changes = (
            _change("src/synthorg/meta/strategies/a.py"),
            _change("src/synthorg/meta/guards/b.py"),
        )
        violations = _validator().validate_changes(changes)
        assert violations == ()

    def test_forbidden_path_returns_violation(self) -> None:
        changes = (
            _change("src/synthorg/meta/strategies/ok.py"),
            _change("src/synthorg/auth/bad.py"),
        )
        violations = _validator().validate_changes(changes)
        assert len(violations) == 1
        assert "auth/bad.py" in violations[0]

    def test_multiple_violations(self) -> None:
        changes = (
            _change("src/synthorg/auth/a.py"),
            _change("src/synthorg/core/security/b.py"),
        )
        violations = _validator().validate_changes(changes)
        assert len(violations) == 2

    def test_empty_changes_returns_empty(self) -> None:
        assert _validator().validate_changes(()) == ()
