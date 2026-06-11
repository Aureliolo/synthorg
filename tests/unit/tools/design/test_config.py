"""Tests for design tool configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.tools.design.config import DesignToolsConfig
from tests._shared import JsonDict


@pytest.mark.unit
class TestDesignToolsConfig:
    """Tests for DesignToolsConfig."""

    def test_default_values(self) -> None:
        config = DesignToolsConfig()
        assert config.image_timeout == 60.0
        assert config.max_image_size_bytes == 52_428_800
        assert config.asset_storage_path is None

    def test_frozen(self) -> None:
        config = DesignToolsConfig()
        with pytest.raises(ValidationError):
            config.image_timeout = 30.0  # type: ignore[misc]

    def test_custom_values(self) -> None:
        config = DesignToolsConfig(
            image_timeout=120.0,
            max_image_size_bytes=10_000_000,
            asset_storage_path="/var/data/assets",
        )
        assert config.image_timeout == 120.0
        assert config.max_image_size_bytes == 10_000_000
        assert config.asset_storage_path == "/var/data/assets"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"image_timeout": 0},
            {"image_timeout": 601.0},
            {"max_image_size_bytes": 0},
            {"image_timeout": float("nan")},
            {"image_timeout": float("inf")},
        ],
        ids=[
            "timeout_zero",
            "timeout_above_max",
            "size_zero",
            "timeout_nan",
            "timeout_inf",
        ],
    )
    def test_rejects_invalid_params(self, kwargs: JsonDict) -> None:
        with pytest.raises(ValidationError):
            DesignToolsConfig(**kwargs)

    def test_blank_storage_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DesignToolsConfig(asset_storage_path="   ")
