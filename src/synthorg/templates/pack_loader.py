"""Template pack loading from built-in and user directory sources.

Packs are small, focused template fragments (same schema as full
templates) that can be applied additively to a running org or
composed into templates via the ``uses_packs`` field.

Discovery mirrors the template loader: built-in packs ship inside
the ``synthorg.templates.packs`` package, and user packs live in
``~/.synthorg/template-packs/``.
"""

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from synthorg.config.errors import ConfigLocation
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_PACK_LIST,
    TEMPLATE_PACK_LOAD_NOT_FOUND,
    TEMPLATE_PACK_LOAD_START,
    TEMPLATE_PACK_LOAD_SUCCESS,
)
from synthorg.templates.errors import (
    TemplateNotFoundError,
    TemplateRenderError,
    TemplateValidationError,
)
from synthorg.templates.loader import LoadedTemplate

logger = get_logger(__name__)

_USER_PACKS_DIR = Path.home() / ".synthorg" / "template-packs"

_PACK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")

BUILTIN_PACKS: MappingProxyType[str, str] = MappingProxyType(
    {
        "security-team": "security-team.yaml",
        "data-team": "data-team.yaml",
        "qa-pipeline": "qa-pipeline.yaml",
        "creative-marketing": "creative-marketing.yaml",
        "design-team": "design-team.yaml",
    }
)


@dataclass(frozen=True)
class PackInfo:
    """Summary information about an available template pack.

    Attributes:
        name: Pack identifier (e.g. ``"security-team"``).
        display_name: Human-readable display name.
        description: Short description.
        source: Where the pack was found.
        tags: Free-form categorization tags.
        agent_count: Number of agents defined in the pack.
        department_count: Number of departments defined in the pack.
    """

    name: str
    display_name: str
    description: str
    source: Literal["builtin", "user"]
    tags: tuple[str, ...] = ()
    agent_count: int = 0
    department_count: int = 0

    def __post_init__(self) -> None:  # noqa: D105
        if not self.name or not self.name.strip():
            msg = "PackInfo.name must not be blank"
            raise ValueError(msg)
        if self.agent_count < 0:
            msg = "agent_count must be non-negative"
            raise ValueError(msg)
        if self.department_count < 0:
            msg = "department_count must be non-negative"
            raise ValueError(msg)


def list_builtin_packs() -> tuple[str, ...]:
    """Return names of all built-in packs.

    Returns:
        Sorted tuple of built-in pack names.
    """
    return tuple(sorted(BUILTIN_PACKS))


def list_packs() -> tuple[PackInfo, ...]:
    """Return all available packs (user directory + built-in).

    User packs override built-in ones by name. Sorted by name.

    Returns:
        Sorted tuple of :class:`PackInfo` objects.
    """
    seen: dict[str, PackInfo] = _collect_user_packs()

    # Built-in packs (lower priority).
    for name in sorted(BUILTIN_PACKS):
        if name not in seen:
            try:
                loaded = _load_builtin(name)
                seen[name] = _pack_info_from_loaded(
                    name,
                    loaded,
                    "builtin",
                )
            except (
                TemplateRenderError,
                TemplateValidationError,
                OSError,
            ) as exc:
                logger.warning(
                    TEMPLATE_PACK_LIST,
                    pack_name=name,
                    action="skip_invalid",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

    return tuple(info for _, info in sorted(seen.items()))


def _validate_pack_name(name: str) -> str:
    """Normalize and validate a pack name via allowlist regex.

    Returns:
        Normalized (lowercase, stripped) name.

    Raises:
        TemplateNotFoundError: If the name does not match the
            allowlist pattern ``[a-z0-9][a-z0-9_-]*``.
    """
    name_clean = normalize_ascii_lowercase(name)
    if not _PACK_NAME_RE.match(name_clean):
        msg = f"Invalid pack name {name!r}: must match [a-z0-9][a-z0-9_-]*"
        logger.warning(TEMPLATE_PACK_LOAD_NOT_FOUND, pack_name=name)
        raise TemplateNotFoundError(
            msg,
            locations=(ConfigLocation(file_path=f"<pack:{name}>"),),
        )
    return name_clean


def load_pack(name: str) -> LoadedTemplate:
    """Load a template pack by name: user directory first, then builtins.

    Args:
        name: Pack name (e.g. ``"security-team"``).

    Returns:
        :class:`LoadedTemplate` with validated data and raw YAML.

    Raises:
        TemplateNotFoundError: If no pack with *name* exists.
        TemplateRenderError: If the pack YAML cannot be read or parsed.
        TemplateValidationError: If the parsed pack fails schema
            validation.
    """
    name_clean = _validate_pack_name(name)
    logger.debug(TEMPLATE_PACK_LOAD_START, pack_name=name_clean)

    # Try user directory first; fall back to builtin on failure.
    if _USER_PACKS_DIR.is_dir():
        user_path = _USER_PACKS_DIR / f"{name_clean}.yaml"
        if user_path.is_file():
            try:
                result = _load_from_file(user_path)
            except (
                TemplateRenderError,
                TemplateValidationError,
            ) as exc:
                if name_clean in BUILTIN_PACKS:
                    logger.warning(
                        TEMPLATE_PACK_LOAD_NOT_FOUND,
                        pack_name=name_clean,
                        action="user_pack_failed_fallback_builtin",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                else:
                    raise
            else:
                logger.debug(
                    TEMPLATE_PACK_LOAD_SUCCESS,
                    pack_name=name_clean,
                    source="user",
                )
                return result

    # Fall back to builtins.
    if name_clean in BUILTIN_PACKS:
        result = _load_builtin(name_clean)
        logger.debug(
            TEMPLATE_PACK_LOAD_SUCCESS,
            pack_name=name_clean,
            source="builtin",
        )
        return result

    available = list_builtin_packs()
    logger.warning(
        TEMPLATE_PACK_LOAD_NOT_FOUND,
        pack_name=name,
        available=list(available),
    )
    msg = f"Unknown template pack {name!r}. Available: {list(available)}"
    raise TemplateNotFoundError(
        msg,
        locations=(ConfigLocation(file_path=f"<pack:{name}>"),),
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _pack_info_from_loaded(
    name: str,
    loaded: LoadedTemplate,
    source: Literal["builtin", "user"],
) -> PackInfo:
    """Build a :class:`PackInfo` from a loaded pack.

    Returns:
        A ``PackInfo`` describing the loaded pack and its source.
    """
    meta = loaded.template.metadata
    return PackInfo(
        name=name,
        display_name=meta.name,
        description=meta.description,
        source=source,
        tags=meta.tags,
        agent_count=len(loaded.template.agents),
        department_count=len(loaded.template.departments),
    )


def _collect_user_packs() -> dict[str, PackInfo]:
    """Scan user packs directory and return discovered packs.

    Returns:
        Dict of normalized pack name to :class:`PackInfo`.
    """
    seen: dict[str, PackInfo] = {}
    if not _USER_PACKS_DIR.is_dir():
        return seen
    for path in sorted(p for p in _USER_PACKS_DIR.glob("*.yaml") if p.is_file()):
        name = normalize_ascii_lowercase(path.stem)
        if not _PACK_NAME_RE.match(name):
            logger.warning(
                TEMPLATE_PACK_LIST,
                pack_path=str(path),
                action="skip_invalid_name",
            )
            continue
        if name in seen:
            continue  # First file wins for case-duplicate stems.
        try:
            loaded = _load_from_file(path)
            seen[name] = _pack_info_from_loaded(name, loaded, "user")
        except (
            TemplateRenderError,
            TemplateValidationError,
            OSError,
        ) as exc:
            logger.warning(
                TEMPLATE_PACK_LIST,
                pack_path=str(path),
                action="skip_invalid",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    return seen


def _load_builtin(name: str) -> LoadedTemplate:
    """Load a built-in pack by name.

    Returns:
        The validated :class:`LoadedTemplate` for the built-in pack.

    Raises:
        TemplateNotFoundError: When ``name`` is not a known built-in pack.
        TemplateRenderError: When the packaged resource cannot be read.
    """
    # circular: pack_loader -> loader -> pack_loader
    from synthorg.templates.loader import (  # noqa: PLC0415
        _parse_template_yaml,
    )

    filename = BUILTIN_PACKS.get(name)
    if filename is None:
        msg = f"Unknown built-in pack: {name!r}"
        logger.warning(TEMPLATE_PACK_LOAD_NOT_FOUND, pack_name=name)
        raise TemplateNotFoundError(
            msg,
            locations=(ConfigLocation(file_path=f"<builtin-pack:{name}>"),),
        )
    source_name = f"<builtin-pack:{name}>"
    try:
        ref = resources.files("synthorg.templates.packs") / filename
        yaml_text = ref.read_text(encoding="utf-8")
    except (OSError, ImportError, TypeError) as exc:
        msg = f"Failed to read built-in pack resource {filename!r}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            TEMPLATE_PACK_LOAD_NOT_FOUND,
            source=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc
    template = _parse_template_yaml(yaml_text, source_name=source_name)
    return LoadedTemplate(
        template=template,
        raw_yaml=yaml_text,
        source_name=source_name,
    )


def _load_from_file(path: Path) -> LoadedTemplate:
    """Load a pack from a file path.

    Returns:
        The validated :class:`LoadedTemplate` parsed from the file.

    Raises:
        TemplateRenderError: When the file cannot be read or is not valid
            UTF-8.
    """
    # circular: pack_loader -> loader -> pack_loader
    from synthorg.templates.loader import (  # noqa: PLC0415
        _parse_template_yaml,
    )

    source_name = str(path)
    try:
        yaml_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Unable to read pack file: {path}"
        logger.warning(
            TEMPLATE_PACK_LOAD_NOT_FOUND,
            path=str(path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc
    except UnicodeDecodeError as exc:
        msg = f"Pack file is not valid UTF-8: {path}"
        logger.warning(
            TEMPLATE_PACK_LOAD_NOT_FOUND,
            path=str(path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc
    template = _parse_template_yaml(yaml_text, source_name=source_name)
    return LoadedTemplate(
        template=template,
        raw_yaml=yaml_text,
        source_name=source_name,
    )
