"""YAML configuration loader with layered merging and validation."""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from synthorg.config.defaults import default_config_dict
from synthorg.config.errors import (
    ConfigFileNotFoundError,
    ConfigLocation,
    ConfigParseError,
    ConfigValidationError,
)
from synthorg.config.schema import RootConfig
from synthorg.config.utils import deep_merge
from synthorg.observability import get_logger
from synthorg.observability.events.config import (
    CONFIG_DISCOVERY_FOUND,
    CONFIG_DISCOVERY_STARTED,
    CONFIG_ENV_VAR_RESOLVED,
    CONFIG_LINE_MAP_COMPOSE_FAILED,
    CONFIG_LOADED,
    CONFIG_OVERRIDE_APPLIED,
    CONFIG_PARSE_FAILED,
    CONFIG_VALIDATION_FAILED,
    CONFIG_YAML_NON_SCALAR_KEY,
)

logger = get_logger(__name__)

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+?)(?::-([^}]*))?\}")

_CWD_CONFIG_LOCATIONS: tuple[Path, ...] = (
    Path("synthorg.yaml"),
    Path("config/synthorg.yaml"),
)

_HOME_CONFIG_RELATIVE = Path(".synthorg") / "config.yaml"

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _read_config_text(file_path: Path) -> str:
    """Read a configuration file as UTF-8 text.

    Args:
        file_path: Path to the configuration file.

    Returns:
        File content as a string.

    Raises:
        ConfigFileNotFoundError: If *file_path* is not a regular file.
        ConfigParseError: If the file cannot be read due to OS errors.
    """
    if not file_path.is_file():
        msg = f"Configuration file not found: {file_path}"
        raise ConfigFileNotFoundError(
            msg,
            locations=(ConfigLocation(file_path=str(file_path)),),
        )
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Unable to read configuration file: {file_path}"
        raise ConfigParseError(
            msg,
            locations=(ConfigLocation(file_path=str(file_path)),),
        ) from exc


def _parse_yaml_file(file_path: Path) -> dict[str, Any]:
    """Parse a YAML file and return its top-level mapping.

    Args:
        file_path: Path to the YAML file.

    Returns:
        Parsed dict (empty dict for ``null`` / empty files).

    Raises:
        ConfigFileNotFoundError: If *file_path* is not a regular file.
        ConfigParseError: If the file cannot be read or contains invalid
            YAML or its top-level value is not a mapping.
    """
    text = _read_config_text(file_path)
    return _parse_yaml_string(text, str(file_path))


def _parse_yaml_string(
    text: str,
    source_name: str,
) -> dict[str, Any]:
    """Parse a YAML string and return its top-level mapping.

    Args:
        text: Raw YAML content.
        source_name: Label used in error messages (file path or
            ``"<string>"``).

    Returns:
        Parsed dict (empty dict for ``null`` / empty strings).

    Raises:
        ConfigParseError: If the text is invalid YAML or its top-level
            value is not a mapping.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        line: int | None = None
        col: int | None = None
        if hasattr(exc, "problem_mark") and exc.problem_mark is not None:
            line = exc.problem_mark.line + 1
            col = exc.problem_mark.column + 1
        msg = f"YAML syntax error in {source_name}: {exc}"
        logger.warning(
            CONFIG_PARSE_FAILED,
            source=source_name,
            line=line,
            column=col,
        )
        raise ConfigParseError(
            msg,
            locations=(
                ConfigLocation(
                    file_path=source_name,
                    line=line,
                    column=col,
                ),
            ),
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"Expected YAML mapping at top level, got {type(data).__name__}"
        raise ConfigParseError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        )
    return data


def _walk_node(
    node: yaml.Node,
    prefix: str,
    result: dict[str, tuple[int, int]],
) -> None:
    """Recursively traverse a composed YAML node tree.

    Populates *result* with ``dot.path`` -> ``(line, column)`` entries
    for each mapping value (keyed by dot-separated path) and each
    sequence element (indexed numerically).

    Args:
        node: YAML AST node to traverse.
        prefix: Dot-path prefix for the current tree level.
        result: Accumulator dict (mutated in place) mapping dot-paths
            to ``(line, column)`` tuples.
    """
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                key: str = key_node.value
                path = f"{prefix}.{key}" if prefix else key
                mark = value_node.start_mark
                if mark is not None:
                    result[path] = (mark.line + 1, mark.column + 1)
                _walk_node(value_node, path, result)
            else:
                logger.debug(
                    CONFIG_YAML_NON_SCALAR_KEY,
                    key_type=type(key_node).__name__,
                )
    elif isinstance(node, yaml.SequenceNode):
        for idx, item_node in enumerate(node.value):
            path = f"{prefix}.{idx}"
            mark = item_node.start_mark
            if mark is not None:
                result[path] = (mark.line + 1, mark.column + 1)
            _walk_node(item_node, path, result)


def _build_line_map(yaml_text: str) -> dict[str, tuple[int, int]]:
    """Build a mapping from dot-path keys to ``(line, column)`` pairs.

    Uses :func:`yaml.compose` to walk the raw YAML AST without
    constructing Python objects, extracting positional information for
    each key path.

    Args:
        yaml_text: Raw YAML content.

    Returns:
        Dict mapping ``"dot.path"`` strings to ``(line, column)`` tuples
        (both 1-based).  Returns an empty dict if the YAML cannot be
        composed.
    """
    try:
        root = yaml.compose(yaml_text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        logger.warning(
            CONFIG_LINE_MAP_COMPOSE_FAILED,
            error=str(exc),
        )
        return {}
    if root is None or not isinstance(root, yaml.MappingNode):
        return {}
    result: dict[str, tuple[int, int]] = {}
    _walk_node(root, "", result)
    return result


def _validate_config_dict(
    data: dict[str, Any],
    *,
    source_file: str | None = None,
    line_map: dict[str, tuple[int, int]] | None = None,
) -> RootConfig:
    """Validate a raw config dict against :class:`RootConfig`.

    Args:
        data: Merged configuration dict.
        source_file: File path label for error messages.
        line_map: Dot-path to (line, col) mapping for error enrichment.

    Returns:
        Validated, frozen :class:`RootConfig`.

    Raises:
        ConfigValidationError: If Pydantic validation fails.
    """
    try:
        return RootConfig(**data)
    except ValidationError as exc:
        logger.warning(
            CONFIG_VALIDATION_FAILED,
            source=source_file,
            error_count=len(exc.errors()),
        )
        if line_map is None:
            line_map = {}
        locations: list[ConfigLocation] = []
        field_errors: list[tuple[str, str]] = []
        for error in exc.errors():
            key_path = ".".join(str(p) for p in error["loc"])
            error_msg = error["msg"]
            field_errors.append((key_path, error_msg))
            line_col = line_map.get(key_path)
            locations.append(
                ConfigLocation(
                    file_path=source_file,
                    key_path=key_path,
                    line=line_col[0] if line_col else None,
                    column=line_col[1] if line_col else None,
                ),
            )
        msg = "Configuration validation failed"
        raise ConfigValidationError(
            msg,
            locations=tuple(locations),
            field_errors=tuple(field_errors),
        ) from exc


def _resolve_env_var_match(
    match: re.Match[str],
    *,
    source_file: str | None,
) -> str:
    """Resolve a single ``${VAR}`` or ``${VAR:-default}`` match.

    Args:
        match: Regex match from :data:`_ENV_VAR_PATTERN`.
        source_file: File path label for error messages.

    Returns:
        Resolved environment variable value or default.

    Raises:
        ConfigValidationError: If the env var is not set and no
            default is provided.
    """
    var_name = match.group(1)
    default = match.group(2)
    value = os.environ.get(var_name)
    if value is not None:
        logger.debug(CONFIG_ENV_VAR_RESOLVED, var_name=var_name)
        return value
    if default is not None:
        return default
    msg = f"Environment variable '{var_name}' is not set and no default was provided"
    raise ConfigValidationError(
        msg,
        locations=(ConfigLocation(file_path=source_file),),
    )


def _walk_substitute(node: Any, *, source_file: str | None) -> Any:
    """Recursively substitute env var placeholders in a config node.

    Args:
        node: Config value (str, dict, list, or scalar).
        source_file: File path label for error messages.

    Returns:
        Node with all ``${VAR}`` placeholders resolved.
    """
    if isinstance(node, str):
        return _ENV_VAR_PATTERN.sub(
            lambda m: _resolve_env_var_match(m, source_file=source_file),
            node,
        )
    if isinstance(node, dict):
        return {
            key: _walk_substitute(value, source_file=source_file)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_walk_substitute(item, source_file=source_file) for item in node]
    return node


def _substitute_env_vars(
    data: dict[str, Any],
    *,
    source_file: str | None = None,
) -> dict[str, Any]:
    """Substitute ``${VAR}`` and ``${VAR:-default}`` in string values.

    Walks the dict recursively, replacing environment variable
    placeholders in string values.  Non-string values (int, float,
    bool, None) are passed through unchanged.  Returns a new dict;
    the input is never mutated.

    Args:
        data: Configuration dict to process.
        source_file: File path label for error messages.

    Returns:
        A new dict with all env var placeholders resolved.

    Raises:
        ConfigValidationError: If a referenced env var is not set
            and no default is provided.
    """
    result: dict[str, Any] = _walk_substitute(data, source_file=source_file)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_config() -> Path:
    """Auto-discover a configuration file from well-known locations.

    Search order:

    1. ``./synthorg.yaml``
    2. ``./config/synthorg.yaml``
    3. ``~/.synthorg/config.yaml``

    Returns:
        Resolved absolute :class:`~pathlib.Path` to the first file found.

    Raises:
        ConfigFileNotFoundError: If no configuration file is found
            at any searched location.
    """
    candidates = [*_CWD_CONFIG_LOCATIONS, Path.home() / _HOME_CONFIG_RELATIVE]
    logger.debug(
        CONFIG_DISCOVERY_STARTED,
        searched_paths=[str(c) for c in candidates],
    )
    for candidate in candidates:
        if candidate.is_file():
            resolved = candidate.resolve()
            logger.info(CONFIG_DISCOVERY_FOUND, config_path=str(resolved))
            return resolved

    searched = [str(c) for c in candidates]
    msg = "No configuration file found. Searched:\n" + "\n".join(
        f"  - {p}" for p in searched
    )
    raise ConfigFileNotFoundError(
        msg,
        locations=tuple(ConfigLocation(file_path=p) for p in searched),
    )


def load_config(
    config_path: Path | str | None = None,
    *,
    override_paths: tuple[Path | str, ...] = (),
) -> RootConfig:
    """Load and validate company configuration from YAML file(s).

    Each layer deep-merges onto the previous: built-in defaults,
    primary config, overrides, then env-var substitution.

    Args:
        config_path: Path to the primary config file, or ``None``
            to auto-discover.
        override_paths: Additional config files layered on top.

    Returns:
        Validated, frozen :class:`RootConfig`.

    Raises:
        ConfigFileNotFoundError: If any config file does not exist
            or discovery finds nothing.
        ConfigParseError: If any file contains invalid YAML.
        ConfigValidationError: If the merged config fails validation.
    """
    if config_path is None:
        config_path = discover_config()
    config_path = Path(config_path)

    # Start with defaults, merge primary config
    merged = default_config_dict()
    yaml_text = _read_config_text(config_path)
    primary = _parse_yaml_string(yaml_text, str(config_path))
    merged = deep_merge(merged, primary)

    # Apply overrides and env-var substitution
    merged = _load_and_merge_overrides(merged, override_paths)
    merged = _substitute_env_vars(merged, source_file="<merged config>")

    return _finalize_config(merged, yaml_text, config_path, override_paths)


def _load_and_merge_overrides(
    merged: dict[str, Any],
    override_paths: tuple[Path | str, ...],
) -> dict[str, Any]:
    """Apply override config files onto the merged dict."""
    for override_path in override_paths:
        override = _parse_yaml_file(Path(override_path))
        merged = deep_merge(merged, override)
        logger.debug(
            CONFIG_OVERRIDE_APPLIED,
            override_path=str(override_path),
        )
    return merged


def _finalize_config(
    merged: dict[str, Any],
    yaml_text: str,
    config_path: Path,
    override_paths: tuple[Path | str, ...],
) -> RootConfig:
    """Validate merged config and log success."""
    line_map = _build_line_map(yaml_text)
    result = _validate_config_dict(
        merged,
        source_file=str(config_path),
        line_map=line_map,
    )
    logger.info(
        CONFIG_LOADED,
        config_path=str(config_path),
        override_count=len(override_paths),
    )
    return result


def bootstrap_logging(config: RootConfig | None = None) -> None:
    """Activate the observability pipeline after config is loaded.

    Calls :func:`~synthorg.observability.configure_logging` with
    ``config.logging``, or sensible defaults if *config* is ``None``.
    Should be called **once** at startup after :func:`load_config`
    returns.

    Args:
        config: Validated root configuration.  When ``None``, the
            logging system uses default settings.
    """
    from synthorg.observability import configure_logging  # noqa: PLC0415

    log_cfg = config.logging if config is not None else None
    configure_logging(log_cfg)


def load_config_from_string(
    yaml_string: str,
    *,
    source_name: str = "<string>",
) -> RootConfig:
    """Load and validate config from a YAML string.

    Merges with built-in defaults before validation.  Useful for API
    endpoints and testing.

    Args:
        yaml_string: Raw YAML content.
        source_name: Label used in error messages.

    Returns:
        Validated, frozen :class:`RootConfig`.

    Raises:
        ConfigParseError: If the YAML is invalid.
        ConfigValidationError: If the merged config fails validation.
    """
    data = _parse_yaml_string(yaml_string, source_name)
    merged = deep_merge(default_config_dict(), data)
    merged = _substitute_env_vars(merged, source_file=source_name)
    line_map = _build_line_map(yaml_string)
    return _validate_config_dict(
        merged,
        source_file=source_name,
        line_map=line_map,
    )
