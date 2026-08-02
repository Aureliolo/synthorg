"""Shared fake-repo builders for the compose-set-or-live lint suite.

The gate cross-references four trees (settings definitions, settings
subscribers, the subsystem registry, and every consumer under
``src/synthorg/``) plus the dashboard, so a fixture has to lay out all of
them. Building that here keeps the test files about the rule rather than
about scaffolding. Not collected by pytest: the underscore prefix keeps it
out of the discovery walk.
"""

import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
"""Project root for the real-repo smoke test."""

_EMPTY_REGISTRY = """
from synthorg.api.subsystems.spec import SubsystemSpec

SUBSYSTEMS: tuple[SubsystemSpec, ...] = ()
"""


def make_repo(
    tmp_path: Path,
    *,
    definitions: dict[str, str],
    sources: dict[str, str] | None = None,
    subscribers: dict[str, str] | None = None,
    registry: str | None = None,
    web: dict[str, str] | None = None,
) -> Path:
    """Create a minimal fake-synthorg tree the gate can scan.

    Args:
        tmp_path: Directory to build the tree under; also the repo root.
        definitions: ``settings/definitions/`` file name to module body.
        sources: Path relative to ``src/synthorg/`` to module body.
        subscribers: ``settings/subscribers/`` file name to module body.
        registry: Body of ``api/subsystems/registry.py``; a registry
            declaring no subsystems when omitted.
        web: Path relative to ``web/src/`` to file body.

    Returns:
        The repo root (``tmp_path`` itself).
    """
    src_root = tmp_path / "src" / "synthorg"
    definitions_dir = src_root / "settings" / "definitions"
    definitions_dir.mkdir(parents=True)
    subscribers_dir = src_root / "settings" / "subscribers"
    subscribers_dir.mkdir(parents=True)
    registry_path = src_root / "api" / "subsystems" / "registry.py"
    registry_path.parent.mkdir(parents=True)
    _write(registry_path, registry if registry is not None else _EMPTY_REGISTRY)
    for name, body in definitions.items():
        _write(definitions_dir / name, body)
    for name, body in (subscribers or {}).items():
        _write(subscribers_dir / name, body)
    for rel, body in (sources or {}).items():
        _write(src_root / rel, body)
    for rel, body in (web or {}).items():
        _write(tmp_path / "web" / "src" / rel, body)
    return tmp_path


def _write(path: Path, body: str) -> None:
    """Write *body* to *path*, creating parents and trimming indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")


def registration(
    key: str,
    *,
    namespace_member: str = "ENGINE",
    namespace_expr: str | None = None,
    compose_set: bool = False,
) -> str:
    """Render one ``_r.register(SettingDefinition(...))`` block.

    Args:
        key: The setting key.
        namespace_member: ``SettingNamespace`` member name, used when
            *namespace_expr* is not given.
        namespace_expr: Verbatim expression for the ``namespace=`` value,
            for exercising alias and enum-attribute resolution.
        compose_set: Whether to add ``compose_set=True``.

    Returns:
        The rendered registration block.
    """
    namespace = namespace_expr or f"SettingNamespace.{namespace_member}"
    lines = [
        "_r.register(",
        "    SettingDefinition(",
        f"        namespace={namespace},",
        f'        key="{key}",',
        "        type=SettingType.BOOLEAN,",
        '        default="false",',
        '        description="...",',
        '        group="General",',
    ]
    if compose_set:
        lines.append("        compose_set=True,")
    lines.extend(["    )", ")"])
    return "\n".join(lines) + "\n"


def definitions_module(*blocks: str, preamble: str = "") -> str:
    """Wrap registration blocks into a definitions module body.

    Args:
        blocks: Rendered registration blocks.
        preamble: Extra module-level source placed after the imports, for
            declaring namespace aliases or registration helpers.

    Returns:
        The rendered module body.
    """
    head = textwrap.dedent("""
        from synthorg.settings.enums import SettingNamespace, SettingType
        from synthorg.settings.models import SettingDefinition
        from synthorg.settings.registry import get_registry

        _r = get_registry()
    """).lstrip("\n")
    return "\n".join([head, preamble, *blocks]) + "\n"


def subscriber_module(*pairs: tuple[str, str]) -> str:
    """Render a settings subscriber declaring a ``_WATCHED`` frozenset.

    Args:
        pairs: The ``(namespace, key)`` pairs the subscriber watches.

    Returns:
        The rendered module body.
    """
    lines = [
        "_WATCHED: frozenset[tuple[str, str]] = frozenset(",
        "    {",
        *(f'        ("{ns}", "{key}"),' for ns, key in pairs),
        "    }",
        ")",
    ]
    return "\n".join(lines) + "\n"


def registry_module(
    *,
    activate: str = "_activate_thing",
    target_module: str = "synthorg.api.lifecycle_helpers.thing_wiring",
    target_function: str = "wire_thing",
    settings: tuple[str, ...] = (),
) -> str:
    """Render a subsystem registry declaring one spec.

    Args:
        activate: Name of the activation wrapper in the registry module.
        target_module: Module the wrapper imports the real wiring from.
        target_function: Wiring function the wrapper calls.
        settings: Dotted ``"ns.key"`` entries for the spec's ``settings=``.

    Returns:
        The rendered module body.
    """
    lines = [
        "from synthorg.api.subsystems.spec import CapabilityId, SubsystemSpec",
        "",
        "",
        f"async def {activate}(app_state: object) -> None:",
        f"    from {target_module} import {target_function}",
        "",
        f"    await {target_function}(app_state)",
        "",
        "",
        "SUBSYSTEMS: tuple[SubsystemSpec, ...] = (",
        "    SubsystemSpec(",
        '        name="thing",',
        "        provides=CapabilityId.THING,",
        f"        activate={activate},",
        "        settings=(",
        *(f'            "{entry}",' for entry in settings),
        "        ),",
        "    ),",
        ")",
    ]
    return "\n".join(lines) + "\n"
