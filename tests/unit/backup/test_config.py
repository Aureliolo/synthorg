"""Tests for backup configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.backup.config import BackupConfig, RetentionConfig
from synthorg.backup.models import BackupComponent

# -- RetentionConfig ----------------------------------------------------------


@pytest.mark.unit
class TestRetentionConfigDefaults:
    def test_default_max_count(self) -> None:
        cfg = RetentionConfig()
        assert cfg.max_count == 10

    def test_default_max_age_days(self) -> None:
        cfg = RetentionConfig()
        assert cfg.max_age_days == 30


@pytest.mark.unit
class TestRetentionConfigBounds:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_count", 0),
            ("max_count", -1),
            ("max_count", 1001),
            ("max_age_days", 0),
            ("max_age_days", -5),
            ("max_age_days", 366),
        ],
    )
    def test_rejects_out_of_range(self, field: str, value: int) -> None:
        with pytest.raises(ValidationError):
            RetentionConfig(**{field: value})

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_count", 1),
            ("max_count", 1000),
            ("max_age_days", 1),
            ("max_age_days", 365),
        ],
    )
    def test_accepts_boundary_values(self, field: str, value: int) -> None:
        cfg = RetentionConfig(**{field: value})
        assert getattr(cfg, field) == value


@pytest.mark.unit
class TestRetentionConfigFrozen:
    def test_cannot_mutate_max_count(self) -> None:
        cfg = RetentionConfig()
        with pytest.raises(ValidationError):
            cfg.max_count = 99  # type: ignore[misc]

    def test_cannot_mutate_max_age_days(self) -> None:
        cfg = RetentionConfig()
        with pytest.raises(ValidationError):
            cfg.max_age_days = 99  # type: ignore[misc]


# -- BackupConfig defaults ---------------------------------------------------


@pytest.mark.unit
class TestBackupConfigDefaults:
    def test_enabled_defaults_true(self) -> None:
        cfg = BackupConfig()
        assert cfg.enabled is True

    def test_default_path(self) -> None:
        cfg = BackupConfig()
        assert cfg.path == "/data/backups"

    def test_default_schedule_hours(self) -> None:
        cfg = BackupConfig()
        assert cfg.schedule_hours == 6

    def test_default_retention_is_retention_config(self) -> None:
        cfg = BackupConfig()
        assert isinstance(cfg.retention, RetentionConfig)

    def test_default_on_shutdown(self) -> None:
        cfg = BackupConfig()
        assert cfg.on_shutdown is False

    def test_default_on_startup(self) -> None:
        # CFG-1 audit flipped the default to False; scheduled backups
        # cover the same guarantee without surprise writes at boot.
        cfg = BackupConfig()
        assert cfg.on_startup is False

    def test_default_compression(self) -> None:
        cfg = BackupConfig()
        assert cfg.compression is True

    def test_default_include_components(self) -> None:
        cfg = BackupConfig()
        assert cfg.include == ("persistence", "memory", "config")


# -- BackupConfig frozen ------------------------------------------------------


@pytest.mark.unit
class TestBackupConfigFrozen:
    def test_cannot_mutate_enabled(self) -> None:
        cfg = BackupConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = True  # type: ignore[misc]

    def test_cannot_mutate_path(self) -> None:
        cfg = BackupConfig()
        with pytest.raises(ValidationError):
            cfg.path = "/other"  # type: ignore[misc]

    def test_cannot_mutate_schedule_hours(self) -> None:
        cfg = BackupConfig()
        with pytest.raises(ValidationError):
            cfg.schedule_hours = 12  # type: ignore[misc]


# -- BackupConfig schedule_hours range ----------------------------------------


@pytest.mark.unit
class TestBackupConfigScheduleHours:
    @pytest.mark.parametrize("hours", [0, -1, 169, 200])
    def test_rejects_out_of_range(self, hours: int) -> None:
        with pytest.raises(ValidationError):
            BackupConfig(schedule_hours=hours)

    @pytest.mark.parametrize("hours", [1, 168, 24, 72])
    def test_accepts_valid_range(self, hours: int) -> None:
        cfg = BackupConfig(schedule_hours=hours)
        assert cfg.schedule_hours == hours


# -- BackupConfig path traversal ----------------------------------------------


@pytest.mark.unit
class TestBackupConfigPathTraversal:
    @pytest.mark.parametrize(
        "path",
        [
            "/data/../etc/passwd",
            "../secrets",
            "/backup/../../root",
            "foo/../bar",
            "..\\secrets",
            "C:\\backup\\..\\escape",
        ],
    )
    def test_rejects_path_with_dotdot(self, path: str) -> None:
        with pytest.raises(ValidationError, match="parent-directory traversal"):
            BackupConfig(path=path)

    @pytest.mark.parametrize(
        "path",
        [
            "/data/backups",
            "/var/lib/synthorg/backup",
            "backups",
            "/data/my.backup.dir",
        ],
    )
    def test_accepts_safe_paths(self, path: str) -> None:
        cfg = BackupConfig(path=path)
        assert cfg.path == path


# -- BackupConfig include validation ------------------------------------------


@pytest.mark.unit
class TestBackupConfigIncludeValidation:
    def test_rejects_unknown_component(self) -> None:
        with pytest.raises(ValidationError, match="Input should be"):
            BackupConfig(include=(BackupComponent.PERSISTENCE, "invalid_thing"))  # type: ignore[arg-type]

    def test_rejects_all_unknown_components(self) -> None:
        with pytest.raises(ValidationError, match="Input should be"):
            BackupConfig(include=("bogus",))  # type: ignore[arg-type]

    @pytest.mark.parametrize("component", list(BackupComponent))
    def test_accepts_each_valid_component(self, component: BackupComponent) -> None:
        cfg = BackupConfig(include=(component,))
        assert component in cfg.include

    def test_accepts_all_components_together(self) -> None:
        all_components = tuple(BackupComponent)
        cfg = BackupConfig(include=all_components)
        assert cfg.include == all_components

    def test_accepts_empty_include(self) -> None:
        cfg = BackupConfig(include=())
        assert cfg.include == ()

    def test_rejects_duplicate_components(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate components"):
            BackupConfig(
                include=(
                    BackupComponent.PERSISTENCE,
                    BackupComponent.PERSISTENCE,
                ),
            )
