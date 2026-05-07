"""Constitutional principle pack loading and validation.

Loads anti-trendslop principle packs from built-in YAML files or
user-provided packs in ``~/.synthorg/strategy-packs/``.  Mirrors the
discovery pattern used by :mod:`synthorg.templates.pack_loader`.
"""

import re
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

import yaml
from pydantic import ValidationError as PydanticValidationError

from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.engine.strategy.models import (
    ConstitutionalPrinciple,
    ConstitutionalPrincipleConfig,
    PrinciplePack,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.strategy import (
    STRATEGY_PACK_INVALID,
    STRATEGY_PACK_LOADED,
    STRATEGY_PACK_NOT_FOUND,
)

logger = get_logger(__name__)

_USER_PACKS_DIR = Path.home() / ".synthorg" / "strategy-packs"

_PACK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")

BUILTIN_PACKS: MappingProxyType[str, str] = MappingProxyType(
    {
        "default": "default.yaml",
        "startup": "startup.yaml",
        "enterprise": "enterprise.yaml",
        "cost_sensitive": "cost_sensitive.yaml",
    }
)


class StrategyPackNotFoundError(NotFoundError):
    """Raised when a requested principle pack cannot be found."""

    default_message: ClassVar[str] = "Strategy pack not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404


class StrategyPackValidationError(ValidationError):
    """Raised when a principle pack fails schema validation."""

    default_message: ClassVar[str] = "Strategy pack validation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422


def _validate_pack_name(name: str) -> str:
    """Normalize and validate a pack name.

    Returns:
        Normalized (lowercase, stripped) name.

    Raises:
        StrategyPackNotFoundError: If the name does not match the
            allowlist pattern ``[a-z0-9][a-z0-9_-]*``.
    """
    name_clean = name.strip().lower()
    if not _PACK_NAME_RE.match(name_clean):
        msg = f"Invalid pack name {name!r}: must match [a-z0-9][a-z0-9_-]*"
        logger.warning(STRATEGY_PACK_NOT_FOUND, pack_name=name)
        raise StrategyPackNotFoundError(msg)
    return name_clean


def _parse_pack_yaml(
    yaml_text: str,
    *,
    source_name: str,
) -> PrinciplePack:
    """Parse YAML text into a validated PrinciplePack.

    Args:
        yaml_text: Raw YAML content.
        source_name: Identifier for error messages.

    Returns:
        Validated :class:`PrinciplePack`.

    Raises:
        StrategyPackValidationError: If parsing or validation fails.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        msg = f"Failed to parse YAML from {source_name}: {safe_error_description(exc)}"
        logger.warning(
            STRATEGY_PACK_INVALID,
            source=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise StrategyPackValidationError(msg) from exc

    if not isinstance(data, dict):
        msg = (
            f"Pack YAML from {source_name} must be a mapping, got {type(data).__name__}"
        )
        logger.warning(
            STRATEGY_PACK_INVALID,
            source=source_name,
            error=msg,
        )
        raise StrategyPackValidationError(msg)

    try:
        principles_raw: list[dict[str, Any]] = data.get("principles", [])
        principles = tuple(ConstitutionalPrinciple(**p) for p in principles_raw)
        return PrinciplePack(
            name=data.get("name", "unknown"),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            principles=principles,
        )
    except (TypeError, ValueError, KeyError, PydanticValidationError) as exc:
        msg = f"Validation failed for pack from {source_name}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            STRATEGY_PACK_INVALID,
            source=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise StrategyPackValidationError(msg) from exc


def _load_builtin(name: str) -> PrinciplePack:
    """Load a built-in principle pack by name.

    Args:
        name: Normalized pack name.

    Returns:
        Validated :class:`PrinciplePack`.

    Raises:
        StrategyPackNotFoundError: If the pack is not a known builtin.
        StrategyPackValidationError: If the pack fails parsing/validation.
    """
    filename = BUILTIN_PACKS.get(name)
    if filename is None:
        msg = f"Unknown built-in strategy pack: {name!r}"
        logger.warning(STRATEGY_PACK_NOT_FOUND, pack_name=name)
        raise StrategyPackNotFoundError(msg)

    source_name = f"<builtin-strategy-pack:{name}>"
    try:
        ref = resources.files("synthorg.engine.strategy.packs") / filename
        yaml_text = ref.read_text(encoding="utf-8")
    except (OSError, ImportError, TypeError) as exc:
        msg = f"Failed to read built-in pack resource {filename!r}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            STRATEGY_PACK_NOT_FOUND,
            source=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise StrategyPackValidationError(msg) from exc

    return _parse_pack_yaml(yaml_text, source_name=source_name)


def _load_from_file(path: Path) -> PrinciplePack:
    """Load a principle pack from a file path.

    Args:
        path: Path to the YAML file.

    Returns:
        Validated :class:`PrinciplePack`.

    Raises:
        StrategyPackValidationError: If the file cannot be read or
            parsed.
    """
    source_name = str(path)
    try:
        yaml_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Unable to read strategy pack file: {path}"
        logger.warning(
            STRATEGY_PACK_NOT_FOUND,
            path=str(path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise StrategyPackValidationError(msg) from exc
    except UnicodeDecodeError as exc:
        msg = f"Strategy pack file is not valid UTF-8: {path}"
        logger.warning(
            STRATEGY_PACK_NOT_FOUND,
            path=str(path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise StrategyPackValidationError(msg) from exc

    return _parse_pack_yaml(yaml_text, source_name=source_name)


def load_pack(name: str) -> PrinciplePack:
    """Load a principle pack by name: user directory first, then builtins.

    Args:
        name: Pack name (e.g. ``"default"``, ``"startup"``).

    Returns:
        Validated :class:`PrinciplePack`.

    Raises:
        StrategyPackNotFoundError: If no pack with *name* exists.
        StrategyPackValidationError: If the pack fails validation.
    """
    name_clean = _validate_pack_name(name)

    # Try user directory first.
    if _USER_PACKS_DIR.is_dir():
        user_path = _USER_PACKS_DIR / f"{name_clean}.yaml"
        if user_path.is_file():
            try:
                result = _load_from_file(user_path)
            except StrategyPackValidationError:
                if name_clean in BUILTIN_PACKS:
                    logger.warning(
                        STRATEGY_PACK_INVALID,
                        pack_name=name_clean,
                        source="user",
                        action="fallback_to_builtin",
                    )
                else:
                    raise
            else:
                logger.debug(
                    STRATEGY_PACK_LOADED,
                    pack_name=name_clean,
                    source="user",
                )
                return result

    # Fall back to builtins.
    if name_clean in BUILTIN_PACKS:
        result = _load_builtin(name_clean)
        logger.debug(
            STRATEGY_PACK_LOADED,
            pack_name=name_clean,
            source="builtin",
        )
        return result

    available = sorted(BUILTIN_PACKS)
    logger.warning(
        STRATEGY_PACK_NOT_FOUND,
        pack_name=name,
        available=list(available),
    )
    msg = f"Unknown strategy pack {name!r}. Available: {list(available)}"
    raise StrategyPackNotFoundError(msg)


def load_and_merge(
    config: ConstitutionalPrincipleConfig,
) -> tuple[ConstitutionalPrinciple, ...]:
    """Load a principle pack and merge with custom principles.

    Args:
        config: Principle configuration with pack name and custom
            principles.

    Returns:
        Tuple of all principles (pack + custom), deduplicated by ID.
    """
    pack = load_pack(config.pack)
    principles = list(pack.principles)

    # Merge custom principles, skipping duplicates by ID.
    existing_ids = {p.id for p in principles}
    for i, raw in enumerate(config.custom):
        try:
            principle = ConstitutionalPrinciple(**raw)
        except (TypeError, PydanticValidationError) as exc:
            msg = (
                f"Invalid custom principle at index {i}: {safe_error_description(exc)}"
            )
            logger.warning(
                STRATEGY_PACK_INVALID,
                source="custom",
                index=i,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise StrategyPackValidationError(msg) from exc
        if principle.id not in existing_ids:
            principles.append(principle)
            existing_ids.add(principle.id)

    return tuple(principles)


def list_builtin_packs() -> tuple[str, ...]:
    """Return names of all built-in strategy packs.

    Returns:
        Sorted tuple of built-in pack names.
    """
    return tuple(sorted(BUILTIN_PACKS))
