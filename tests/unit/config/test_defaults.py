"""Tests for config defaults."""

from typing import Any

import pytest

from synthorg.config.defaults import default_config_dict
from synthorg.config.schema import RootConfig


@pytest.mark.unit
class TestDefaultConfigDict:
    def test_returns_dict(self) -> None:
        result = default_config_dict()
        assert isinstance(result, dict)

    def test_required_keys_present(self) -> None:
        result = default_config_dict()
        assert "company_name" in result
        assert "company_type" in result
        assert result["company_name"] == "SynthOrg"
        assert result["company_type"] == "custom"

    def test_constructs_valid_root_config(self) -> None:
        data: dict[str, Any] = default_config_dict()  # narrow for **unpacking
        cfg = RootConfig(**data)
        assert cfg.company_name == "SynthOrg"
        assert cfg.company_type.value == "custom"

    def test_returns_fresh_dict_each_call(self) -> None:
        a = default_config_dict()
        b = default_config_dict()
        assert a == b
        assert a is not b

    def test_keys_match_root_config_fields(self) -> None:
        defaults = default_config_dict()
        root_fields = set(RootConfig.model_fields.keys())
        default_keys = set(defaults.keys())
        missing = root_fields - default_keys
        extra = default_keys - root_fields
        assert not missing, f"Defaults missing keys for RootConfig fields: {missing}"
        assert not extra, f"Defaults has extra keys not in RootConfig: {extra}"
