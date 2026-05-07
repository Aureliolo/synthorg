"""Workflow blueprint loading from built-in and user directory sources.

Blueprints are starter workflow graph templates that users can
instantiate as ``WorkflowDefinition`` objects.  Discovery mirrors
the template pack loader: built-in blueprints ship inside the
``synthorg.engine.workflow.blueprints`` package, and user blueprints
live in ``~/.synthorg/workflow-blueprints/``.
"""

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from synthorg.engine.workflow.blueprint_errors import (
    BlueprintNotFoundError,
    BlueprintValidationError,
)
from synthorg.engine.workflow.blueprint_models import BlueprintData
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.blueprint import (
    BLUEPRINT_LIST,
    BLUEPRINT_LOAD_NOT_FOUND,
    BLUEPRINT_LOAD_START,
    BLUEPRINT_LOAD_SUCCESS,
)

logger = get_logger(__name__)

_USER_BLUEPRINTS_DIR = Path.home() / ".synthorg" / "workflow-blueprints"

_BLUEPRINT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")

BUILTIN_BLUEPRINTS: MappingProxyType[str, str] = MappingProxyType(
    {
        "feature-pipeline": "feature-pipeline.yaml",
        "bug-fix-flow": "bug-fix-flow.yaml",
        "research-sprint": "research-sprint.yaml",
        "code-review-pipeline": "code-review-pipeline.yaml",
        "onboarding-workflow": "onboarding-workflow.yaml",
        "verification-pipeline": "verification-pipeline.yaml",
    }
)


@dataclass(frozen=True)
class BlueprintInfo:
    """Summary information about an available workflow blueprint.

    Attributes:
        name: Blueprint identifier (e.g. ``"feature-pipeline"``).
        display_name: Human-readable display name.
        description: Short description.
        source: Where the blueprint was found.
        tags: Categorization tags.
        workflow_type: Target workflow type.
        node_count: Number of nodes in the graph.
        edge_count: Number of edges in the graph.
    """

    name: str
    display_name: str
    description: str
    source: Literal["builtin", "user"]
    tags: tuple[str, ...] = ()
    workflow_type: str = "sequential_pipeline"
    node_count: int = 0
    edge_count: int = 0

    def __post_init__(self) -> None:  # noqa: D105
        if not self.name or not self.name.strip():
            msg = "BlueprintInfo.name must not be blank"
            raise ValueError(msg)
        if not self.display_name or not self.display_name.strip():
            msg = "BlueprintInfo.display_name must not be blank"
            raise ValueError(msg)
        if self.node_count < 0:
            msg = "node_count must be non-negative"
            raise ValueError(msg)
        if self.edge_count < 0:
            msg = "edge_count must be non-negative"
            raise ValueError(msg)


def list_builtin_blueprints() -> tuple[str, ...]:
    """Return names of all built-in blueprints.

    Returns:
        Sorted tuple of built-in blueprint names.
    """
    return tuple(sorted(BUILTIN_BLUEPRINTS))


def list_blueprints() -> tuple[BlueprintInfo, ...]:
    """Return all available blueprints (user directory + built-in).

    User blueprints override built-in ones by name.  Sorted by name.

    Returns:
        Sorted tuple of :class:`BlueprintInfo` objects.
    """
    seen: dict[str, BlueprintInfo] = _collect_user_blueprints()

    for name in sorted(BUILTIN_BLUEPRINTS):
        if name not in seen:
            try:
                data = _load_builtin(name)
                seen[name] = _blueprint_info_from_data(data, "builtin")
            except BlueprintNotFoundError, BlueprintValidationError, OSError:
                logger.warning(
                    BLUEPRINT_LIST,
                    blueprint_name=name,
                    action="skip_invalid",
                )

    return tuple(info for _, info in sorted(seen.items()))


def load_blueprint(name: str) -> BlueprintData:
    """Load a workflow blueprint by name.

    Tries the user directory first, then falls back to built-in.

    Args:
        name: Blueprint name (e.g. ``"feature-pipeline"``).

    Returns:
        Validated :class:`BlueprintData`.

    Raises:
        BlueprintNotFoundError: If no blueprint with *name* exists.
        BlueprintValidationError: If the YAML fails validation.
    """
    name_clean = _validate_blueprint_name(name)
    logger.debug(BLUEPRINT_LOAD_START, blueprint_name=name_clean)

    user_result = _try_load_user_blueprint(name_clean)
    if user_result is not None:
        return user_result

    # Fall back to builtins.
    if name_clean in BUILTIN_BLUEPRINTS:
        result = _load_builtin(name_clean)
        logger.debug(
            BLUEPRINT_LOAD_SUCCESS,
            blueprint_name=name_clean,
            source="builtin",
        )
        return result

    available = list_builtin_blueprints()
    logger.warning(
        BLUEPRINT_LOAD_NOT_FOUND,
        blueprint_name=name,
        available=list(available),
    )
    msg = f"Unknown workflow blueprint {name!r}. Available: {list(available)}"
    raise BlueprintNotFoundError(msg)


def _try_load_user_blueprint(name: str) -> BlueprintData | None:
    """Attempt to load a blueprint from the user directory.

    Performs symlink escape checking before loading.

    Args:
        name: Validated blueprint name.

    Returns:
        Loaded blueprint data, or ``None`` if not found
        in the user directory.

    Raises:
        BlueprintValidationError: If found but invalid.
    """
    if not _USER_BLUEPRINTS_DIR.is_dir():
        return None

    user_path = _USER_BLUEPRINTS_DIR / f"{name}.yaml"
    if not user_path.is_file():
        return None

    # Guard against symlink escape (TOCTOU hardening).
    resolved_base = _USER_BLUEPRINTS_DIR.resolve()
    resolved = user_path.resolve()
    if not resolved.is_relative_to(resolved_base):
        logger.warning(
            BLUEPRINT_LIST,
            blueprint_path=str(user_path),
            action="skip_symlink_escape",
        )
        return None

    try:
        result = _load_from_file(user_path, name)
    except (BlueprintValidationError, OSError) as exc:
        if name in BUILTIN_BLUEPRINTS:
            logger.warning(
                BLUEPRINT_LOAD_NOT_FOUND,
                blueprint_name=name,
                action="user_failed_fallback_builtin",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        raise

    logger.debug(
        BLUEPRINT_LOAD_SUCCESS,
        blueprint_name=name,
        source="user",
    )
    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _validate_blueprint_name(name: str) -> str:
    """Normalize and validate a blueprint name.

    Returns:
        Normalized (lowercase, stripped) name.

    Raises:
        BlueprintNotFoundError: If the name does not match the
            allowlist pattern ``[a-z0-9][a-z0-9_-]*``.
    """
    name_clean = name.strip().lower()
    if not _BLUEPRINT_NAME_RE.match(name_clean):
        msg = f"Invalid blueprint name {name!r}: must match [a-z0-9][a-z0-9_-]*"
        logger.warning(BLUEPRINT_LOAD_NOT_FOUND, blueprint_name=name)
        raise BlueprintNotFoundError(msg)
    return name_clean


def _blueprint_info_from_data(
    data: BlueprintData,
    source: Literal["builtin", "user"],
) -> BlueprintInfo:
    """Build a :class:`BlueprintInfo` from validated data."""
    return BlueprintInfo(
        name=data.name,
        display_name=data.display_name,
        description=data.description,
        source=source,
        tags=data.tags,
        workflow_type=data.workflow_type.value,
        node_count=len(data.nodes),
        edge_count=len(data.edges),
    )


def _parse_blueprint_yaml(yaml_text: str, source_name: str) -> BlueprintData:
    """Parse and validate a blueprint YAML string.

    Raises:
        BlueprintValidationError: On YAML parse or schema errors.
    """
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        msg = f"Failed to parse YAML from {source_name}: {safe_error_description(exc)}"
        logger.warning(
            BLUEPRINT_LIST,
            source=source_name,
            action="yaml_parse_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise BlueprintValidationError(msg) from exc

    if not isinstance(raw, dict) or "blueprint" not in raw:
        msg = f"Blueprint YAML from {source_name} must have a top-level 'blueprint' key"
        logger.warning(
            BLUEPRINT_LIST,
            source=source_name,
            action="missing_blueprint_key",
        )
        raise BlueprintValidationError(msg)

    blueprint_dict: dict[str, Any] = raw["blueprint"]
    try:
        return BlueprintData.model_validate(blueprint_dict)
    except (ValueError, TypeError, ValidationError) as exc:
        msg = f"Blueprint validation failed for {source_name}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            BLUEPRINT_LIST,
            source=source_name,
            action="schema_validation_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise BlueprintValidationError(msg) from exc


def _load_builtin(name: str) -> BlueprintData:
    """Load a built-in blueprint by name."""
    filename = BUILTIN_BLUEPRINTS.get(name)
    if filename is None:
        msg = f"Unknown built-in blueprint: {name!r}"
        logger.warning(BLUEPRINT_LOAD_NOT_FOUND, blueprint_name=name)
        raise BlueprintNotFoundError(msg)

    source_name = f"<builtin-blueprint:{name}>"
    try:
        ref = resources.files("synthorg.engine.workflow.blueprints") / filename
        yaml_text = ref.read_text(encoding="utf-8")
    except (OSError, ImportError, TypeError) as exc:
        msg = f"Failed to read built-in blueprint {filename!r}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            BLUEPRINT_LOAD_NOT_FOUND,
            source=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise BlueprintValidationError(msg) from exc

    return _parse_blueprint_yaml(yaml_text, source_name)


def _load_from_file(path: Path, name: str) -> BlueprintData:
    """Load a blueprint from a file path."""
    source_name = str(path)
    try:
        yaml_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Unable to read blueprint file: {path}"
        logger.warning(
            BLUEPRINT_LOAD_NOT_FOUND,
            path=str(path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise BlueprintValidationError(msg) from exc
    except UnicodeDecodeError as exc:
        msg = f"Blueprint file is not valid UTF-8: {path}"
        logger.warning(
            BLUEPRINT_LOAD_NOT_FOUND,
            path=str(path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise BlueprintValidationError(msg) from exc

    data = _parse_blueprint_yaml(yaml_text, source_name)
    # Verify the parsed name matches the file stem.
    if data.name != name:
        msg = (
            f"Blueprint name mismatch in {source_name}: "
            f"file stem is {name!r} but YAML declares "
            f"{data.name!r}"
        )
        logger.warning(
            BLUEPRINT_LIST,
            blueprint_name=name,
            parsed_name=data.name,
            action="name_mismatch_rejected",
        )
        raise BlueprintValidationError(msg)
    return data


def _collect_user_blueprints() -> dict[str, BlueprintInfo]:
    """Scan user blueprints directory and return discovered blueprints."""
    seen: dict[str, BlueprintInfo] = {}
    if not _USER_BLUEPRINTS_DIR.is_dir():
        return seen
    resolved_base = _USER_BLUEPRINTS_DIR.resolve()
    for path in sorted(p for p in _USER_BLUEPRINTS_DIR.glob("*.yaml") if p.is_file()):
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_base):
            logger.warning(
                BLUEPRINT_LIST,
                blueprint_path=str(path),
                action="skip_symlink_escape",
            )
            continue
        name = path.stem.strip().lower()
        if not _BLUEPRINT_NAME_RE.match(name):
            logger.warning(
                BLUEPRINT_LIST,
                blueprint_path=str(path),
                action="skip_invalid_name",
            )
            continue
        if name in seen:
            continue
        try:
            data = _load_from_file(path, name)
            seen[name] = _blueprint_info_from_data(data, "user")
        except (BlueprintValidationError, OSError) as exc:
            logger.warning(
                BLUEPRINT_LIST,
                blueprint_path=str(path),
                action="skip_invalid",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    return seen
