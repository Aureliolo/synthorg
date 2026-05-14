"""Service layer for personality preset discovery and CRUD.

Merges builtin presets (from code) with user-defined custom presets
(from persistence) behind a single async interface.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from synthorg.core.agent import PersonalityConfig
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.preset import (
    PRESET_CONFLICT,
    PRESET_CREATED,
    PRESET_DELETED,
    PRESET_NOT_FOUND,
    PRESET_UPDATED,
    PRESET_VALIDATION_FAILED,
)
from synthorg.persistence.preset_protocol import (
    PersonalityPresetRepository,  # noqa: TC001
)
from synthorg.templates.preset_models import PresetSource
from synthorg.templates.presets import PERSONALITY_PRESETS

logger = get_logger(__name__)

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class PresetEntry(BaseModel):
    """A personality preset with source metadata.

    Attributes:
        name: Lowercase preset identifier.
        source: Whether the preset is builtin or user-defined.
        config: Full personality configuration as a dict.
        description: Human-readable description.
        created_at: ISO 8601 creation timestamp (None for builtins).
        updated_at: ISO 8601 last-update timestamp (None for builtins).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    name: NotBlankStr
    source: PresetSource
    config: dict[str, Any]
    description: str = ""
    created_at: str | None = None
    updated_at: str | None = None


def _builtin_to_entry(name: str, preset: dict[str, Any]) -> PresetEntry:
    """Convert a builtin preset dict to a PresetEntry."""
    return PresetEntry(
        name=NotBlankStr(name),
        source=PresetSource.BUILTIN,
        config=dict(preset),
        description=str(preset.get("description", "")),
    )


def _normalize_preset_name(raw: str) -> str:
    """Normalize a preset name to lowercase with whitespace stripped.

    Args:
        raw: Raw name input.

    Returns:
        Normalized lowercase name.

    Raises:
        ValidationError: If the name is empty or has invalid format.
    """
    name = normalize_ascii_lowercase(raw)
    if not name:
        logger.warning(
            PRESET_VALIDATION_FAILED,
            raw_name=raw,
            reason="blank",
        )
        msg = "Preset name must not be blank"
        raise ValidationError(msg)
    if not _NAME_PATTERN.match(name):
        logger.warning(
            PRESET_VALIDATION_FAILED,
            raw_name=raw,
            normalized=name,
            reason="invalid_format",
        )
        msg = (
            f"Invalid preset name {name!r}. "
            "Must match [a-z][a-z0-9_]* (lowercase, underscores only)."
        )
        raise ValidationError(msg)
    return name


def _validate_personality_config(
    config: dict[str, Any],
) -> PersonalityConfig:
    """Validate a config dict against PersonalityConfig.

    Args:
        config: Raw personality configuration fields.

    Returns:
        Validated PersonalityConfig instance.

    Raises:
        ValidationError: If validation fails.
    """
    try:
        return PersonalityConfig(**config)
    except PydanticValidationError as exc:
        logger.warning(
            PRESET_VALIDATION_FAILED,
            reason="invalid_config",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = "Invalid personality configuration: one or more fields failed validation"
        raise ValidationError(msg) from exc


def _parse_config_json(config_json: str, preset_name: str) -> dict[str, Any]:
    """Parse a JSON config string from the database.

    Args:
        config_json: Serialized personality config.
        preset_name: Name of the preset (for error context).

    Returns:
        Parsed config dict.

    Raises:
        NotFoundError: If the JSON is corrupt.
    """
    try:
        parsed: dict[str, Any] = json.loads(config_json)
    except json.JSONDecodeError as exc:
        logger.warning(
            PRESET_VALIDATION_FAILED,
            preset_name=preset_name,
            reason="corrupt_json",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Personality preset {preset_name!r} has corrupt configuration"
        raise NotFoundError(msg) from exc
    return parsed


def _check_not_builtin(key: str, operation: str) -> None:
    """Raise ConflictError if the name matches a builtin preset.

    Args:
        key: Normalized preset name.
        operation: Operation name for logging context.
    """
    if key in PERSONALITY_PRESETS:
        logger.warning(
            PRESET_CONFLICT,
            preset_name=key,
            reason=f"{operation}_builtin",
        )
        msg = f"Cannot {operation} builtin preset {key!r}"
        raise ConflictError(msg)


class PersonalityPresetService:
    """Merges builtin and custom presets behind one async interface.

    Args:
        repository: Persistence layer for custom presets.
    """

    def __init__(self, repository: PersonalityPresetRepository) -> None:
        self._repo = repository

    async def list_all(self) -> tuple[PresetEntry, ...]:
        """Return all presets (builtin + custom), sorted by name.

        Returns:
            Tuple of preset entries tagged with their source.
        """
        entries: dict[str, PresetEntry] = {}

        for name, preset in PERSONALITY_PRESETS.items():
            entries[name] = _builtin_to_entry(name, dict(preset))

        for row in await self._repo.list_all():
            config = _parse_config_json(row.config_json, row.name)
            entries[row.name] = PresetEntry(
                name=NotBlankStr(row.name),
                source=PresetSource.CUSTOM,
                config=config,
                description=row.description,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

        return tuple(entries[k] for k in sorted(entries))

    async def get(self, name: str) -> PresetEntry:
        """Look up a preset by name (case-insensitive).

        Custom presets take precedence over builtins with the same name.

        Args:
            name: Preset identifier.

        Returns:
            The matching preset entry.

        Raises:
            NotFoundError: If no preset with this name exists.
        """
        key = normalize_ascii_lowercase(name)
        if not key:
            logger.warning(PRESET_NOT_FOUND, preset_name=name, reason="blank")
            msg = f"Personality preset {name!r} not found"
            raise NotFoundError(msg)

        row = await self._repo.get(NotBlankStr(key))
        if row is not None:
            config = _parse_config_json(row.config_json, key)
            return PresetEntry(
                name=NotBlankStr(key),
                source=PresetSource.CUSTOM,
                config=config,
                description=row.description,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

        if key in PERSONALITY_PRESETS:
            return _builtin_to_entry(key, dict(PERSONALITY_PRESETS[key]))

        logger.warning(PRESET_NOT_FOUND, preset_name=key)
        msg = f"Personality preset {name!r} not found"
        raise NotFoundError(msg)

    async def create(
        self,
        name: str,
        config: dict[str, Any],
    ) -> PresetEntry:
        """Create a new custom preset.

        Args:
            name: Preset identifier (normalized to lowercase).
            config: Personality configuration fields.

        Returns:
            The created preset entry.

        Raises:
            ConflictError: If name shadows a builtin preset or a
                custom preset with the same name already exists.
            ValidationError: If name format or config is invalid.
        """
        key = _normalize_preset_name(name)
        _check_not_builtin(key, "create")

        # TOCTOU: get-then-save is not atomic. Under concurrent requests
        # two creates may both pass and the second silently overwrites via
        # the repo's upsert. Acceptable for MVP with low concurrency;
        # consider transactional INSERT or locking if stricter semantics
        # are needed (_repo.get, _repo.save).
        existing = await self._repo.get(NotBlankStr(key))
        if existing is not None:
            logger.warning(
                PRESET_CONFLICT,
                preset_name=key,
                reason="already_exists",
            )
            msg = f"Custom preset {key!r} already exists"
            raise ConflictError(msg)

        validated = _validate_personality_config(config)
        config_json = json.dumps(
            validated.model_dump(mode="json"),
            sort_keys=True,
        )
        description = str(config.get("description", ""))
        now = datetime.now(UTC).isoformat()

        await self._repo.save(
            NotBlankStr(key),
            config_json,
            description,
            now,
            now,
        )
        logger.info(PRESET_CREATED, preset_name=key)

        return PresetEntry(
            name=NotBlankStr(key),
            source=PresetSource.CUSTOM,
            config=validated.model_dump(mode="json"),
            description=description,
            created_at=now,
            updated_at=now,
        )

    async def update(
        self,
        name: str,
        config: dict[str, Any],
    ) -> PresetEntry:
        """Update an existing custom preset.

        Args:
            name: Preset identifier.
            config: Updated personality configuration fields.

        Returns:
            The updated preset entry.

        Raises:
            ConflictError: If name is a builtin preset.
            NotFoundError: If no custom preset with this name exists.
            ValidationError: If name or config is invalid.
        """
        key = _normalize_preset_name(name)
        _check_not_builtin(key, "update")

        # TOCTOU: get-then-save is not atomic. A concurrent delete could
        # remove the preset between the check and save, causing a silent
        # re-create via upsert. Same trade-off as create() above.
        existing = await self._repo.get(NotBlankStr(key))
        if existing is None:
            logger.warning(
                PRESET_NOT_FOUND,
                preset_name=key,
                operation="update",
            )
            msg = f"Custom preset {name!r} not found"
            raise NotFoundError(msg)

        validated = _validate_personality_config(config)
        config_json = json.dumps(
            validated.model_dump(mode="json"),
            sort_keys=True,
        )
        description = str(config.get("description", ""))
        now = datetime.now(UTC).isoformat()

        await self._repo.save(
            NotBlankStr(key),
            config_json,
            description,
            existing.created_at,
            now,
        )
        logger.info(PRESET_UPDATED, preset_name=key)

        return PresetEntry(
            name=NotBlankStr(key),
            source=PresetSource.CUSTOM,
            config=validated.model_dump(mode="json"),
            description=description,
            created_at=existing.created_at,
            updated_at=now,
        )

    async def delete(self, name: str) -> None:
        """Delete a custom preset.

        Args:
            name: Preset identifier.

        Raises:
            ConflictError: If name is a builtin preset.
            NotFoundError: If no custom preset with this name exists.
            ValidationError: If name format is invalid.
        """
        key = _normalize_preset_name(name)
        _check_not_builtin(key, "delete")

        deleted = await self._repo.delete(NotBlankStr(key))
        if not deleted:
            logger.warning(
                PRESET_NOT_FOUND,
                preset_name=key,
                operation="delete",
            )
            msg = f"Custom preset {name!r} not found"
            raise NotFoundError(msg)

        logger.info(PRESET_DELETED, preset_name=key)

    @staticmethod
    def get_schema() -> dict[str, Any]:
        """Return the JSON Schema for PersonalityConfig.

        Returns:
            JSON Schema dict.
        """
        return PersonalityConfig.model_json_schema()


async def fetch_custom_presets_map(
    repo: PersonalityPresetRepository,
) -> dict[str, dict[str, Any]]:
    """Fetch all custom presets as a sync-friendly name-to-config dict.

    This bridges the async persistence layer and the sync template
    rendering pipeline.  Call once before rendering and pass the
    result as ``custom_presets`` to :func:`render_template` or
    :func:`expand_template_agents`.

    Rows with corrupt JSON are logged and skipped -- a single bad
    row does not prevent the remaining presets from loading.

    Args:
        repo: Personality preset repository.

    Returns:
        Mapping of lowercased preset names to personality config dicts.
    """
    rows = await repo.list_all()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = normalize_ascii_lowercase(str(row.name))
        try:
            decoded = json.loads(row.config_json)
        except json.JSONDecodeError as exc:
            # Bind the exception and use the structured-warning form
            # so the full traceback doesn't bypass redaction.
            logger.warning(
                PRESET_VALIDATION_FAILED,
                preset_name=row.name,
                reason="corrupt_json_in_fetch_map",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        if not isinstance(decoded, dict):
            logger.error(
                PRESET_VALIDATION_FAILED,
                preset_name=row.name,
                reason="non_object_config_in_fetch_map",
            )
            continue
        result[key] = decoded
    return result
