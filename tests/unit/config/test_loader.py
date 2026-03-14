"""Tests for config loader (parsing, merging, validation)."""

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import structlog

if TYPE_CHECKING:
    from .conftest import ConfigFileFactory

from synthorg.config.errors import (
    ConfigFileNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from synthorg.config.loader import (
    _build_line_map,
    _parse_yaml_file,
    _parse_yaml_string,
    _read_config_text,
    _substitute_env_vars,
    _validate_config_dict,
    discover_config,
    load_config,
    load_config_from_string,
)
from synthorg.config.schema import RootConfig
from synthorg.observability.events.config import (
    CONFIG_LOADED,
    CONFIG_PARSE_FAILED,
    CONFIG_VALIDATION_FAILED,
)

from .conftest import (
    ENV_VAR_MISSING_YAML,
    ENV_VAR_NESTED_YAML,
    ENV_VAR_SIMPLE_YAML,
    FULL_VALID_YAML,
    INVALID_FIELD_VALUES_YAML,
    INVALID_SYNTAX_YAML,
    MINIMAL_VALID_YAML,
    MISSING_REQUIRED_YAML,
)

# ── _read_config_text ────────────────────────────────────────────


@pytest.mark.unit
class TestReadConfigText:
    def test_reads_file(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("company_name: Test\n", encoding="utf-8")
        assert _read_config_text(f) == "company_name: Test\n"

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigFileNotFoundError, match="not found"):
            _read_config_text(tmp_path / "missing.yaml")

    def test_directory_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigFileNotFoundError, match="not found"):
            _read_config_text(tmp_path)

    def test_os_error_wrapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("content", encoding="utf-8")
        monkeypatch.setattr(
            "pathlib.Path.read_text",
            lambda *a, **kw: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(ConfigParseError, match="Unable to read"):
            _read_config_text(f)


# ── _parse_yaml_file ─────────────────────────────────────────────


@pytest.mark.unit
class TestParseYamlFile:
    def test_valid_file(self, tmp_path: Path) -> None:
        f = tmp_path / "config.yaml"
        f.write_text("company_name: Test\n", encoding="utf-8")
        result = _parse_yaml_file(f)
        assert result == {"company_name": "Test"}

    def test_syntax_error(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text(INVALID_SYNTAX_YAML, encoding="utf-8")
        with pytest.raises(ConfigParseError, match="YAML syntax error"):
            _parse_yaml_file(f)

    def test_non_mapping_top_level(self, tmp_path: Path) -> None:
        f = tmp_path / "list.yaml"
        f.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ConfigParseError, match="mapping"):
            _parse_yaml_file(f)

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        assert _parse_yaml_file(f) == {}

    def test_null_file(self, tmp_path: Path) -> None:
        f = tmp_path / "null.yaml"
        f.write_text("null\n", encoding="utf-8")
        assert _parse_yaml_file(f) == {}

    def test_file_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "missing.yaml"
        with pytest.raises(ConfigFileNotFoundError, match="not found"):
            _parse_yaml_file(f)


# ── _parse_yaml_string ───────────────────────────────────────────


@pytest.mark.unit
class TestParseYamlString:
    def test_valid_string(self) -> None:
        result = _parse_yaml_string("key: value\n", "<test>")
        assert result == {"key": "value"}

    def test_syntax_error(self) -> None:
        with pytest.raises(ConfigParseError, match="syntax error"):
            _parse_yaml_string(INVALID_SYNTAX_YAML, "<test>")

    def test_empty_string(self) -> None:
        assert _parse_yaml_string("", "<test>") == {}

    def test_non_mapping(self) -> None:
        with pytest.raises(ConfigParseError, match="mapping"):
            _parse_yaml_string("- a\n- b\n", "<test>")


# ── _build_line_map / _walk_node ─────────────────────────────────


@pytest.mark.unit
class TestBuildLineMap:
    def test_simple_mapping(self) -> None:
        yaml_text = "company_name: Test\nbudget:\n  total_monthly: 100\n"
        result = _build_line_map(yaml_text)
        assert "company_name" in result
        assert "budget" in result
        assert "budget.total_monthly" in result
        assert result["company_name"][0] == 1
        assert result["budget.total_monthly"][0] == 3

    def test_sequence_elements(self) -> None:
        yaml_text = "agents:\n  - name: Alice\n  - name: Bob\n"
        result = _build_line_map(yaml_text)
        assert "agents.0" in result
        assert "agents.1" in result
        assert "agents.0.name" in result

    def test_invalid_yaml_returns_empty(self) -> None:
        result = _build_line_map("invalid: [unterminated\n")
        assert result == {}

    def test_non_mapping_root_returns_empty(self) -> None:
        result = _build_line_map("- item1\n- item2\n")
        assert result == {}

    def test_empty_string_returns_empty(self) -> None:
        result = _build_line_map("")
        assert result == {}

    def test_null_yaml_returns_empty(self) -> None:
        result = _build_line_map("null\n")
        assert result == {}


# ── _validate_config_dict ────────────────────────────────────────


@pytest.mark.unit
class TestValidateConfigDict:
    def test_valid_dict(self) -> None:
        data = {"company_name": "Test Corp"}
        result = _validate_config_dict(data)
        assert isinstance(result, RootConfig)
        assert result.company_name == "Test Corp"

    def test_invalid_dict_raises(self) -> None:
        with pytest.raises(ConfigValidationError) as exc_info:
            _validate_config_dict({"company_name": ""})
        assert exc_info.value.field_errors

    def test_line_map_enriches_errors(self) -> None:
        line_map = {"company_name": (5, 16)}
        with pytest.raises(ConfigValidationError) as exc_info:
            _validate_config_dict(
                {"company_name": ""},
                source_file="test.yaml",
                line_map=line_map,
            )
        err = exc_info.value
        loc = next(
            loc
            for loc in err.locations
            if loc.key_path and "company_name" in loc.key_path
        )
        assert loc.file_path == "test.yaml"
        assert loc.line == 5
        assert loc.column == 16

    def test_none_line_map_gracefully_degrades(self) -> None:
        with pytest.raises(ConfigValidationError) as exc_info:
            _validate_config_dict(
                {"company_name": ""},
                source_file="test.yaml",
                line_map=None,
            )
        err = exc_info.value
        assert err.field_errors
        for loc in err.locations:
            assert loc.line is None
            assert loc.column is None


# ── load_config ──────────────────────────────────────────────────


@pytest.mark.unit
class TestLoadConfig:
    def test_explicit_path(self, tmp_config_file: ConfigFileFactory) -> None:
        path = tmp_config_file(MINIMAL_VALID_YAML)
        cfg = load_config(path)
        assert isinstance(cfg, RootConfig)
        assert cfg.company_name == "Test Corp"

    def test_full_config(self, tmp_config_file: ConfigFileFactory) -> None:
        path = tmp_config_file(FULL_VALID_YAML)
        cfg = load_config(path)
        assert cfg.company_name == "Test Corp"
        assert len(cfg.agents) == 1
        assert cfg.agents[0].name == "Alice"
        assert "example-provider" in cfg.providers

    def test_layered_override(self, tmp_config_file: ConfigFileFactory) -> None:
        base_path = tmp_config_file(
            "company_name: Base Corp\ncompany_type: custom\n",
            name="base.yaml",
        )
        override_path = tmp_config_file(
            "company_name: Override Corp\n",
            name="override.yaml",
        )
        cfg = load_config(base_path, override_paths=(override_path,))
        assert cfg.company_name == "Override Corp"

    def test_multiple_override_files_applied_in_order(
        self, tmp_config_file: ConfigFileFactory
    ) -> None:
        base = tmp_config_file("company_name: Base\n", name="base.yaml")
        over1 = tmp_config_file("company_name: Override1\n", name="over1.yaml")
        over2 = tmp_config_file("company_name: Override2\n", name="over2.yaml")
        cfg = load_config(base, override_paths=(over1, over2))
        assert cfg.company_name == "Override2"

    def test_defaults_applied(self, tmp_config_file: ConfigFileFactory) -> None:
        path = tmp_config_file(MINIMAL_VALID_YAML)
        cfg = load_config(path)
        assert cfg.budget.total_monthly == 100.0
        assert cfg.routing.strategy == "cost_aware"

    def test_validation_error_with_location(
        self, tmp_config_file: ConfigFileFactory
    ) -> None:
        path = tmp_config_file(MISSING_REQUIRED_YAML)
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config(path)
        err = exc_info.value
        assert err.field_errors
        assert any("company_name" in key for key, _ in err.field_errors)

    def test_frozen_result(self, tmp_config_file: ConfigFileFactory) -> None:
        from pydantic import ValidationError

        path = tmp_config_file(MINIMAL_VALID_YAML)
        cfg = load_config(path)
        with pytest.raises(ValidationError):
            cfg.company_name = "Nope"  # type: ignore[misc]

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigFileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_syntax_error(self, tmp_config_file: ConfigFileFactory) -> None:
        path = tmp_config_file(INVALID_SYNTAX_YAML)
        with pytest.raises(ConfigParseError):
            load_config(path)

    def test_nested_override_merge(self, tmp_config_file: ConfigFileFactory) -> None:
        base_path = tmp_config_file(
            "company_name: X\nbudget:\n  total_monthly: 200.0\n",
            name="base.yaml",
        )
        override_path = tmp_config_file(
            "budget:\n  per_task_limit: 10.0\n",
            name="override.yaml",
        )
        cfg = load_config(base_path, override_paths=(override_path,))
        assert cfg.budget.total_monthly == 200.0
        assert cfg.budget.per_task_limit == 10.0

    def test_string_path_accepted(self, tmp_config_file: ConfigFileFactory) -> None:
        """String paths are coerced to Path objects."""
        path = tmp_config_file(MINIMAL_VALID_YAML)
        cfg = load_config(str(path))
        assert cfg.company_name == "Test Corp"

    def test_directory_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigFileNotFoundError):
            load_config(tmp_path)


# ── load_config_from_string ──────────────────────────────────────


@pytest.mark.unit
class TestLoadConfigFromString:
    def test_minimal(self) -> None:
        cfg = load_config_from_string(MINIMAL_VALID_YAML)
        assert cfg.company_name == "Test Corp"
        assert isinstance(cfg, RootConfig)

    def test_full(self) -> None:
        cfg = load_config_from_string(FULL_VALID_YAML)
        assert cfg.company_name == "Test Corp"
        assert len(cfg.agents) == 1
        assert cfg.budget.total_monthly == 500.0

    def test_invalid_yaml(self) -> None:
        with pytest.raises(ConfigParseError):
            load_config_from_string(INVALID_SYNTAX_YAML)

    def test_validation_error(self) -> None:
        with pytest.raises(ConfigValidationError) as exc_info:
            load_config_from_string(INVALID_FIELD_VALUES_YAML)
        assert exc_info.value.field_errors

    def test_defaults_merged(self) -> None:
        cfg = load_config_from_string(MINIMAL_VALID_YAML)
        assert cfg.budget.total_monthly == 100.0

    def test_custom_source_name(self) -> None:
        with pytest.raises(ConfigParseError, match="my-source"):
            load_config_from_string(
                INVALID_SYNTAX_YAML,
                source_name="my-source",
            )

    def test_empty_string_uses_defaults(self) -> None:
        cfg = load_config_from_string("")
        assert cfg.company_name == "SynthOrg"


# ── _substitute_env_vars ────────────────────────────────────────


@pytest.mark.unit
class TestSubstituteEnvVars:
    def test_simple_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOO", "bar")
        data = {"key": "${FOO}"}
        result = _substitute_env_vars(data)
        assert result == {"key": "bar"}

    def test_missing_var_raises(self) -> None:
        data = {"key": "${MISSING_VAR_XYZ}"}
        with pytest.raises(ConfigValidationError, match="MISSING_VAR_XYZ"):
            _substitute_env_vars(data)

    def test_default_used_when_missing(self) -> None:
        data = {"key": "${MISSING_VAR_XYZ:-fallback}"}
        result = _substitute_env_vars(data)
        assert result == {"key": "fallback"}

    def test_default_ignored_when_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SET_VAR", "real")
        data = {"key": "${SET_VAR:-fallback}"}
        result = _substitute_env_vars(data)
        assert result == {"key": "real"}

    def test_empty_default(self) -> None:
        data = {"key": "${MISSING_VAR_XYZ:-}"}
        result = _substitute_env_vars(data)
        assert result == {"key": ""}

    def test_nested_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INNER", "resolved")
        data = {"outer": {"inner": "${INNER}"}}
        result = _substitute_env_vars(data)
        assert result == {"outer": {"inner": "resolved"}}

    def test_list_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITEM", "hello")
        data = {"items": ["${ITEM}", "static"]}
        result = _substitute_env_vars(data)
        assert result == {"items": ["hello", "static"]}

    def test_non_string_unchanged(self) -> None:
        data = {"int": 42, "float": 3.14, "bool": True, "null": None}
        result = _substitute_env_vars(data)
        assert result == {"int": 42, "float": 3.14, "bool": True, "null": None}

    def test_multiple_vars_in_one_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A", "alpha")
        monkeypatch.setenv("B", "beta")
        data = {"key": "${A}:${B}"}
        result = _substitute_env_vars(data)
        assert result == {"key": "alpha:beta"}

    def test_partial_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VAR", "middle")
        data = {"key": "prefix-${VAR}-suffix"}
        result = _substitute_env_vars(data)
        assert result == {"key": "prefix-middle-suffix"}

    def test_input_not_mutated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X", "replaced")
        original = {"key": "${X}", "nested": {"deep": "${X}"}}
        original_copy = {"key": "${X}", "nested": {"deep": "${X}"}}
        _substitute_env_vars(original)
        assert original == original_copy

    def test_no_placeholders_passthrough(self) -> None:
        data = {"key": "no vars here", "num": 123}
        result = _substitute_env_vars(data)
        assert result == {"key": "no vars here", "num": 123}

    def test_deeply_nested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEEP", "found")
        data = {"a": {"b": {"c": {"d": {"e": "${DEEP}"}}}}}
        result = _substitute_env_vars(data)
        assert result == {"a": {"b": {"c": {"d": {"e": "found"}}}}}

    def test_no_recursive_expansion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env var values containing ${...} syntax are NOT recursively expanded."""
        monkeypatch.setenv("OUTER", "${INNER}")
        monkeypatch.setenv("INNER", "should_not_appear")
        data = {"key": "${OUTER}"}
        result = _substitute_env_vars(data)
        assert result == {"key": "${INNER}"}

    def test_special_chars_in_env_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env var values with regex/URL special chars are preserved verbatim."""
        monkeypatch.setenv("URL", "https://example.com/path?a=1&b=2#frag")
        data = {"endpoint": "${URL}"}
        result = _substitute_env_vars(data)
        assert result == {"endpoint": "https://example.com/path?a=1&b=2#frag"}

    def test_missing_var_error_includes_source_file(self) -> None:
        """Error for missing env var includes the source_file in locations."""
        data = {"key": "${MISSING_XYZ}"}
        with pytest.raises(ConfigValidationError) as exc_info:
            _substitute_env_vars(data, source_file="my-config.yaml")
        assert exc_info.value.locations[0].file_path == "my-config.yaml"


# ── discover_config ─────────────────────────────────────────────


@pytest.mark.unit
class TestDiscoverConfig:
    def test_finds_cwd_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "synthorg.yaml"
        config_file.write_text("company_name: Test\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        result = discover_config()
        assert result == config_file.resolve()

    def test_finds_config_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "synthorg.yaml"
        config_file.write_text("company_name: Test\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        result = discover_config()
        assert result == config_file.resolve()

    def test_finds_home_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # CWD has no config
        monkeypatch.chdir(tmp_path)
        # Home dir has config
        fake_home = tmp_path / "fakehome"
        config_dir = fake_home / ".synthorg"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("company_name: Test\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        result = discover_config()
        assert result == config_file.resolve()

    def test_precedence_cwd_over_subdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Both CWD and config/ have files
        cwd_file = tmp_path / "synthorg.yaml"
        cwd_file.write_text("company_name: CWD\n", encoding="utf-8")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        subdir_file = config_dir / "synthorg.yaml"
        subdir_file.write_text("company_name: SubDir\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        result = discover_config()
        assert result == cwd_file.resolve()

    def test_precedence_subdir_over_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        # config/ subdir has file
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        subdir_file = config_dir / "synthorg.yaml"
        subdir_file.write_text("company_name: SubDir\n", encoding="utf-8")
        # Home dir has file
        fake_home = tmp_path / "fakehome"
        home_config_dir = fake_home / ".synthorg"
        home_config_dir.mkdir(parents=True)
        home_file = home_config_dir / "config.yaml"
        home_file.write_text("company_name: Home\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        result = discover_config()
        assert result == subdir_file.resolve()

    def test_no_config_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        with pytest.raises(
            ConfigFileNotFoundError, match="No configuration file"
        ) as exc_info:
            discover_config()
        # All 3 search locations should be reported
        assert len(exc_info.value.locations) == 3

    def test_returns_resolved_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "synthorg.yaml"
        config_file.write_text("company_name: Test\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        result = discover_config()
        assert result.is_absolute()


# ── Env var substitution through load_config ────────────────────


@pytest.mark.unit
class TestLoadConfigEnvVar:
    def test_env_var_in_load_config(
        self, tmp_config_file: ConfigFileFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COMPANY_NAME", "Env Corp")
        path = tmp_config_file(ENV_VAR_SIMPLE_YAML)
        cfg = load_config(path)
        assert cfg.company_name == "Env Corp"

    def test_env_var_with_default_in_load_config(
        self, tmp_config_file: ConfigFileFactory
    ) -> None:
        yaml_content = "company_name: ${UNDEFINED_TEST_VAR:-Default Corp}\n"
        path = tmp_config_file(yaml_content)
        cfg = load_config(path)
        assert cfg.company_name == "Default Corp"

    def test_missing_env_var_raises_in_load_config(
        self, tmp_config_file: ConfigFileFactory
    ) -> None:
        path = tmp_config_file(ENV_VAR_MISSING_YAML)
        with pytest.raises(ConfigValidationError, match="UNDEFINED_VAR"):
            load_config(path)

    def test_env_var_in_nested_config(
        self, tmp_config_file: ConfigFileFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COMPANY_NAME", "Nested Corp")
        monkeypatch.setenv("EXAMPLE_PROVIDER_BASE_URL", "https://custom.api")
        path = tmp_config_file(ENV_VAR_NESTED_YAML)
        cfg = load_config(path)
        assert cfg.company_name == "Nested Corp"
        assert cfg.providers["example-provider"].base_url == "https://custom.api"

    def test_env_var_in_load_config_from_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COMPANY_NAME", "String Corp")
        cfg = load_config_from_string(ENV_VAR_SIMPLE_YAML)
        assert cfg.company_name == "String Corp"

    def test_env_var_default_in_load_config_from_string(self) -> None:
        yaml_content = "company_name: ${UNDEFINED_TEST_VAR:-FromString Corp}\n"
        cfg = load_config_from_string(yaml_content)
        assert cfg.company_name == "FromString Corp"

    def test_missing_env_var_raises_in_load_config_from_string(self) -> None:
        with pytest.raises(ConfigValidationError, match="UNDEFINED_VAR"):
            load_config_from_string(ENV_VAR_MISSING_YAML)

    def test_env_var_in_override_file(
        self, tmp_config_file: ConfigFileFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OVERRIDE_NAME", "Override Corp")
        base = tmp_config_file(MINIMAL_VALID_YAML, name="base.yaml")
        override = tmp_config_file(
            "company_name: ${OVERRIDE_NAME}\n",
            name="override.yaml",
        )
        cfg = load_config(base, override_paths=(override,))
        assert cfg.company_name == "Override Corp"


# ── discover_config with load_config ────────────────────────────


@pytest.mark.unit
class TestLoadConfigDiscovery:
    def test_load_config_none_uses_discovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "synthorg.yaml"
        config_file.write_text(MINIMAL_VALID_YAML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        cfg = load_config(None)
        assert cfg.company_name == "Test Corp"

    def test_load_config_none_no_config_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        with pytest.raises(ConfigFileNotFoundError):
            load_config(None)

    def test_load_config_explicit_path_still_works(
        self, tmp_config_file: ConfigFileFactory
    ) -> None:
        """Backward compatibility: explicit path still works as before."""
        path = tmp_config_file(MINIMAL_VALID_YAML)
        cfg = load_config(path)
        assert cfg.company_name == "Test Corp"


# ── Logging tests ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.timeout(30)
class TestLoaderLogging:
    def test_config_loaded_event_on_success(
        self,
        tmp_config_file: ConfigFileFactory,
    ) -> None:
        path = tmp_config_file(MINIMAL_VALID_YAML)
        with structlog.testing.capture_logs() as cap:
            load_config(path)
        events = [e for e in cap if e.get("event") == CONFIG_LOADED]
        assert len(events) == 1
        assert "config_path" in events[0]

    def test_config_parse_failed_event(self) -> None:
        with structlog.testing.capture_logs() as cap, pytest.raises(ConfigParseError):
            _parse_yaml_string(INVALID_SYNTAX_YAML, "test.yaml")
        events = [e for e in cap if e.get("event") == CONFIG_PARSE_FAILED]
        assert len(events) == 1
        assert events[0]["source"] == "test.yaml"

    def test_config_validation_failed_event(self) -> None:
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(ConfigValidationError),
        ):
            _validate_config_dict(
                {"company_name": 12345},
                source_file="test.yaml",
            )
        events = [e for e in cap if e.get("event") == CONFIG_VALIDATION_FAILED]
        assert len(events) == 1
        assert events[0]["error_count"] >= 1
