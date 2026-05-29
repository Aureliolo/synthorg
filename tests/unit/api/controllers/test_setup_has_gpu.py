"""Tests for ``_read_has_gpu_setting`` in the setup controller.

Split out of ``test_setup.py`` (oversized) so the GPU-setting parsing
helpers live in a focused module.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.unit
class TestReadHasGpuSetting:
    """``_read_has_gpu_setting`` parses every boolean variant safely."""

    @pytest.mark.parametrize(
        ("stored_value", "expected"),
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("YES", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("", False),
            ("maybe", None),
            ("garbage", None),
        ],
    )
    async def test_parses_boolean_variants(
        self,
        stored_value: str,
        expected: bool | None,
    ) -> None:
        from synthorg.api.controllers.setup.completion import _read_has_gpu_setting

        settings_svc = MagicMock()
        entry = MagicMock()
        entry.value = stored_value
        settings_svc.get = AsyncMock(return_value=entry)

        result = await _read_has_gpu_setting(settings_svc)
        assert result is expected

    async def test_missing_setting_returns_false(self) -> None:
        """An empty entry.value resolves to ``False`` (explicit default)."""
        from synthorg.api.controllers.setup.completion import _read_has_gpu_setting

        settings_svc = MagicMock()
        entry = MagicMock()
        entry.value = ""
        settings_svc.get = AsyncMock(return_value=entry)

        result = await _read_has_gpu_setting(settings_svc)
        assert result is False

    async def test_read_failure_returns_none(self) -> None:
        """A raised ``get()`` is swallowed; returns None + logs."""
        from synthorg.api.controllers.setup.completion import _read_has_gpu_setting

        settings_svc = MagicMock()
        settings_svc.get = AsyncMock(side_effect=RuntimeError("backend down"))

        result = await _read_has_gpu_setting(settings_svc)
        assert result is None

    async def test_memory_error_propagates(self) -> None:
        """``MemoryError`` is never swallowed -- propagates untouched."""
        from synthorg.api.controllers.setup.completion import _read_has_gpu_setting

        settings_svc = MagicMock()
        settings_svc.get = AsyncMock(side_effect=MemoryError())

        with pytest.raises(MemoryError):
            await _read_has_gpu_setting(settings_svc)
