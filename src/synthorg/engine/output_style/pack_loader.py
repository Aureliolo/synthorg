# module-kind: adapter
"""Output-style rule-pack loading, validation, and merging.

Loads packs from built-in YAML files or user packs in
``~/.synthorg/output-style-packs/``. Mirrors the strategy principle-pack loader
so the two subsystems stay consistent. Operator exemptions from settings merge
on top of a pack's own default exemptions.
"""

import re
from importlib import resources
from pathlib import Path
from types import MappingProxyType

import yaml
from pydantic import ValidationError as PydanticValidationError

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.engine.output_style.errors import (
    OutputStylePackNotFoundError,
    OutputStylePackValidationError,
)
from synthorg.engine.output_style.models import (
    ALL_RULES,
    PACK_NAME_PATTERN,
    EnforcementMode,
    HouseStyleDirective,
    OutputStyleConfig,
    OutputStyleRule,
    RulePack,
    RuleSeverity,
    RuleType,
    SanctionedExemption,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.output_style import (
    OUTPUT_STYLE_PACK_INVALID,
    OUTPUT_STYLE_PACK_LOADED,
    OUTPUT_STYLE_PACK_NOT_FOUND,
)

logger = get_logger(__name__)

_USER_PACKS_DIR = Path.home() / ".synthorg" / "output-style-packs"

_PACK_NAME_RE = re.compile(PACK_NAME_PATTERN)

BUILTIN_PACKS: MappingProxyType[str, str] = MappingProxyType(
    {
        "default": "default.yaml",
    }
)


def _validate_pack_name(name: str) -> str:
    """Normalise and validate a pack name.

    Returns:
        Normalised (lowercase, stripped) name.

    Raises:
        OutputStylePackNotFoundError: If the name is not in the allowlist
            pattern ``[a-z0-9][a-z0-9_-]*``.
    """
    name_clean = normalize_ascii_lowercase(name)
    if not _PACK_NAME_RE.match(name_clean):
        logger.warning(OUTPUT_STYLE_PACK_NOT_FOUND, pack_name=name)
        msg = f"Invalid pack name {name!r}: must match [a-z0-9][a-z0-9_-]*"
        raise OutputStylePackNotFoundError(msg)
    return name_clean


#: The three canonical HTML entity forms of the em-dash, built at runtime from
#: the ampersand codepoint so no literal entity string sits in committed source
#: (the repo's check_no_em_dashes.py gate bans those forms too).
_AMP: str = chr(38)
_EMDASH_ENTITIES: tuple[str, ...] = (
    _AMP + "mdash;",
    _AMP + "#8212;",
    _AMP + "#x2014;",
)


def _build_rule(raw: object) -> OutputStyleRule:
    """Build a rule, expanding ``codepoints`` / ``emdash_entities`` to literals.

    A pack expresses banned characters by integer codepoint (``codepoints``)
    and the em-dash HTML entities by the ``emdash_entities`` flag, so the YAML
    source never embeds a literal em-dash or entity string. Both expand here to
    ordinary literal patterns before the frozen model is constructed.

    Args:
        raw: The raw rule mapping from the pack YAML.

    Returns:
        The constructed :class:`OutputStyleRule`.

    Raises:
        OutputStylePackValidationError: If ``raw`` is not a mapping or a
            codepoint is not a valid integer.
    """
    if not isinstance(raw, dict):
        msg = f"Each rule must be a mapping, got {type(raw).__name__}"
        raise OutputStylePackValidationError(msg)
    fields = dict(raw)
    codepoints = fields.pop("codepoints", ())
    emdash_entities = bool(fields.pop("emdash_entities", False))
    patterns = list(fields.get("patterns", ()))
    for codepoint in codepoints:
        if not isinstance(codepoint, int) or isinstance(codepoint, bool):
            msg = f"codepoints must be integers, got {codepoint!r}"
            raise OutputStylePackValidationError(msg)
        patterns.append(chr(codepoint))
    if emdash_entities:
        patterns.extend(_EMDASH_ENTITIES)
    fields["patterns"] = tuple(patterns)
    return OutputStyleRule(**fields)


def _parse_pack_yaml(yaml_text: str, *, source_name: str) -> RulePack:
    """Parse YAML text into a validated :class:`RulePack`.

    Args:
        yaml_text: Raw YAML content.
        source_name: Identifier for error messages.

    Returns:
        Validated :class:`RulePack`.

    Raises:
        OutputStylePackValidationError: If parsing or validation fails.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        logger.warning(
            OUTPUT_STYLE_PACK_INVALID,
            source=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse YAML from {source_name}: {safe_error_description(exc)}"
        raise OutputStylePackValidationError(msg) from exc

    if not isinstance(data, dict):
        logger.warning(OUTPUT_STYLE_PACK_INVALID, source=source_name)
        msg = f"Pack YAML from {source_name} must be a mapping"
        raise OutputStylePackValidationError(msg)

    try:
        rules = tuple(_build_rule(r) for r in data.get("rules", []))
        house_style = tuple(
            HouseStyleDirective(**d) for d in data.get("house_style", [])
        )
        exemptions = tuple(SanctionedExemption(**e) for e in data.get("exemptions", []))
        return RulePack(
            name=data.get("name", "unknown"),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            house_style=house_style,
            rules=rules,
            exemptions=exemptions,
        )
    except (TypeError, ValueError, KeyError, PydanticValidationError) as exc:
        logger.warning(
            OUTPUT_STYLE_PACK_INVALID,
            source=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Validation failed for pack from {source_name}: {safe_error_description(exc)}"  # noqa: E501
        raise OutputStylePackValidationError(msg) from exc


def _load_builtin(name: str) -> RulePack:
    """Load a built-in pack by name.

    Args:
        name: Normalised pack name.

    Returns:
        Validated :class:`RulePack`.

    Raises:
        OutputStylePackNotFoundError: If the pack is not a known builtin.
        OutputStylePackValidationError: If the pack fails parsing/validation.
    """
    filename = BUILTIN_PACKS.get(name)
    if filename is None:
        logger.warning(OUTPUT_STYLE_PACK_NOT_FOUND, pack_name=name)
        msg = f"Unknown built-in output-style pack: {name!r}"
        raise OutputStylePackNotFoundError(msg)

    source_name = f"<builtin-output-style-pack:{name}>"
    try:
        ref = resources.files("synthorg.engine.output_style.packs") / filename
        yaml_text = ref.read_text(encoding="utf-8")
    except (OSError, ImportError, TypeError) as exc:
        logger.warning(
            OUTPUT_STYLE_PACK_NOT_FOUND,
            source=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"Failed to read built-in pack {filename!r}: {safe_error_description(exc)}"
        )
        raise OutputStylePackValidationError(msg) from exc

    return _parse_pack_yaml(yaml_text, source_name=source_name)


def _load_from_file(path: Path) -> RulePack:
    """Load a pack from a file path.

    Args:
        path: Path to the YAML file.

    Returns:
        Validated :class:`RulePack`.

    Raises:
        OutputStylePackValidationError: If the file cannot be read or parsed.
    """
    source_name = str(path)
    try:
        yaml_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(
            OUTPUT_STYLE_PACK_NOT_FOUND,
            path=str(path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Unable to read output-style pack file: {path}"
        raise OutputStylePackValidationError(msg) from exc
    return _parse_pack_yaml(yaml_text, source_name=source_name)


def load_pack(name: str) -> RulePack:
    """Load a pack by name: user directory first, then builtins.

    Args:
        name: Pack name (e.g. ``"default"``).

    Returns:
        Validated :class:`RulePack`.

    Raises:
        OutputStylePackNotFoundError: If no pack with *name* exists.
        OutputStylePackValidationError: If the pack fails validation.
    """
    name_clean = _validate_pack_name(name)

    if _USER_PACKS_DIR.is_dir():
        user_path = _USER_PACKS_DIR / f"{name_clean}.yaml"
        if user_path.is_file():
            try:
                result = _load_from_file(user_path)
            except OutputStylePackValidationError:
                if name_clean in BUILTIN_PACKS:
                    logger.warning(
                        OUTPUT_STYLE_PACK_INVALID,
                        pack_name=name_clean,
                        source="user",
                        action="fallback_to_builtin",
                    )
                else:
                    raise
            else:
                logger.debug(
                    OUTPUT_STYLE_PACK_LOADED, pack_name=name_clean, source="user"
                )
                return result

    if name_clean in BUILTIN_PACKS:
        result = _load_builtin(name_clean)
        logger.debug(OUTPUT_STYLE_PACK_LOADED, pack_name=name_clean, source="builtin")
        return result

    available = sorted(BUILTIN_PACKS)
    logger.warning(
        OUTPUT_STYLE_PACK_NOT_FOUND, pack_name=name, available=list(available)
    )
    msg = f"Unknown output-style pack {name!r}. Available: {available}"
    raise OutputStylePackNotFoundError(msg)


def minimal_failclosed_pack() -> RulePack:
    """Build the in-code fail-closed pack (the em-dash hard ban only).

    Used as the ultimate fallback when even the built-in default pack cannot be
    loaded (a corrupted packaged resource), so the load-bearing em-dash ban
    keeps enforcing rather than the guardrail silently disabling. Built from the
    codepoint at runtime so no literal em-dash sits in committed source.

    Returns:
        A one-rule pack rejecting the em-dash and its HTML entity forms.
    """
    rule = OutputStyleRule(
        id="emdash_literal",
        type=RuleType.LITERAL_BAN,
        patterns=(chr(0x2014), *_EMDASH_ENTITIES),
        message="Em-dash (U+2014) is banned; use a comma, colon, or period.",
        mode=EnforcementMode.REJECT_REWORK,
        severity=RuleSeverity.CRITICAL,
    )
    return RulePack(
        name="failclosed",
        version="builtin",
        description="In-code fail-closed pack: em-dash hard ban only.",
        rules=(rule,),
    )


def merge_exemptions(
    pack: RulePack, config: OutputStyleConfig
) -> tuple[SanctionedExemption, ...]:
    """Return the pack's default exemptions plus the operator's exemptions.

    An operator exemption naming a rule the active pack does not define can
    never fire; it is kept (packs are swappable) but logged so a typo is
    visible rather than silently inert.

    Args:
        pack: The loaded rule pack.
        config: The operator config carrying additional exemptions.

    Returns:
        All sanctioned exemptions in effect (pack first, operator second).
    """
    known = {rule.id for rule in pack.rules} | {ALL_RULES}
    for exemption in config.exemptions:
        if exemption.rule_id not in known:
            logger.warning(
                OUTPUT_STYLE_PACK_INVALID,
                source="operator_exemption",
                note="exemption targets a rule the active pack does not define",
                rule_id=exemption.rule_id,
                pack_name=pack.name,
            )
    return (*pack.exemptions, *config.exemptions)


def list_builtin_packs() -> tuple[str, ...]:
    """Return names of all built-in output-style packs.

    Returns:
        Sorted tuple of built-in pack names.
    """
    return tuple(sorted(BUILTIN_PACKS))


__all__ = [
    "BUILTIN_PACKS",
    "list_builtin_packs",
    "load_pack",
    "merge_exemptions",
    "minimal_failclosed_pack",
]
