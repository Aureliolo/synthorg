# module-kind: code
"""Named operating postures and their feature-flag expansion.

A template declares a :class:`~synthorg.templates.enums.PostureName`; a
:class:`PostureExpansionStrategy` resolves it to a frozen
:class:`~synthorg.config.posture_config.PostureConfig` of runtime feature
flags. The default :class:`NamedBundlePostureStrategy` reads a curated
registry, so postures are data with a pluggable resolution seam (a factory
keyed by a discriminator) per the project's pluggable-everything convention.

``PostureConfig`` is the RootConfig-resident model: ``_config_assembly`` threads
the resolved flags into ``RootConfig`` (security / budget) and the
setup-completion seeder writes the settings-resident flags (chat modes,
steering) that the best-effort boot wiring reads. A posture never imports or
wires a subsystem itself.
"""

from collections.abc import Callable
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from synthorg.config.posture_config import PostureConfig
from synthorg.observability import get_logger
from synthorg.observability.events.template import TEMPLATE_POSTURE_EXPANDED
from synthorg.templates.enums import PostureName
from synthorg.templates.errors import TemplatePostureError
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

__all__ = [
    "NamedBundlePostureStrategy",
    "PostureExpansionStrategy",
    "get_posture_strategy",
    "resolve_template_posture",
]


def _bundle(name: PostureName, **flags: bool | str) -> PostureConfig:
    """Build a named ``PostureConfig`` for the registry.

    Returns:
        A ``PostureConfig`` with ``name`` set and the given flags applied.
    """
    return PostureConfig.model_validate({"name": name.value, **flags})


# Curated flag bundle per named posture. Data, not behaviour: the seeder and
# ``_config_assembly`` translate each flag into its real knob.
_POSTURE_BUNDLES: MappingProxyType[PostureName, PostureConfig] = MappingProxyType(
    {
        PostureName.AUTONOMOUS: _bundle(
            PostureName.AUTONOMOUS,
            knowledge_substrate=True,
            steering=True,
        ),
        PostureName.SUPERVISED_CLIENT_FACING: _bundle(
            PostureName.SUPERVISED_CLIENT_FACING,
            chat_propose=True,
            chat_routing=True,
            group_chat=True,
            agent_invite=True,
            steering=True,
        ),
        PostureName.KNOWLEDGE_HEAVY: _bundle(
            PostureName.KNOWLEDGE_HEAVY,
            knowledge_substrate=True,
            chat_propose=True,
            steering=True,
        ),
        PostureName.COST_DISCIPLINED: _bundle(
            PostureName.COST_DISCIPLINED,
            auto_downgrade=True,
        ),
        PostureName.SECURITY_HARDENED: _bundle(
            PostureName.SECURITY_HARDENED,
            knowledge_substrate=True,
            red_team=True,
            red_team_grounding="knowledge_substrate",
            steering=True,
        ),
        PostureName.RESEARCH_AUTONOMOUS: _bundle(
            PostureName.RESEARCH_AUTONOMOUS,
            knowledge_substrate=True,
            chat_propose=True,
            chat_routing=True,
            steering=True,
        ),
    },
)


@runtime_checkable
class PostureExpansionStrategy(Protocol):
    """Resolves a posture name to its runtime feature-flag config."""

    def expand(self, name: PostureName) -> PostureConfig:
        """Return the :class:`PostureConfig` for *name*."""
        ...


class NamedBundlePostureStrategy:
    """Default strategy: look up a curated bundle from a frozen registry."""

    def __init__(
        self,
        bundles: MappingProxyType[PostureName, PostureConfig] = _POSTURE_BUNDLES,
    ) -> None:
        self._bundles = bundles

    def expand(self, name: PostureName) -> PostureConfig:
        """Return the curated posture config for *name*.

        Returns:
            The :class:`PostureConfig` registered for *name*.

        Raises:
            TemplatePostureError: When *name* has no registered bundle.
        """
        bundle = self._bundles.get(name)
        if bundle is None:
            msg = f"No feature bundle registered for posture {name.value!r}"
            logger.warning(TEMPLATE_POSTURE_EXPANDED, posture=name.value, found=False)
            raise TemplatePostureError(msg)
        logger.debug(TEMPLATE_POSTURE_EXPANDED, posture=name.value, found=True)
        return bundle


_DEFAULT_STRATEGY: PostureExpansionStrategy = NamedBundlePostureStrategy()


def get_posture_strategy(kind: str = "named") -> PostureExpansionStrategy:
    """Return the posture-expansion strategy for *kind*.

    Args:
        kind: Strategy discriminator (config-selectable). Only ``"named"``
            ships today; the seam exists so a deployment can register an
            alternative resolver.

    Returns:
        The selected :class:`PostureExpansionStrategy` (default singleton for
        ``"named"``).

    Raises:
        TemplatePostureError: When *kind* is not a known strategy.
    """
    if kind == "named":
        return _DEFAULT_STRATEGY
    msg = f"Unknown posture strategy {kind!r}"
    raise TemplatePostureError(msg)


def resolve_template_posture(
    template: CompanyTemplate,
    *,
    load_pack: Callable[[str], CompanyTemplate],
    load_parent: Callable[[str], CompanyTemplate],
    strategy: PostureExpansionStrategy | None = None,
) -> PostureConfig | None:
    """Resolve a template's effective posture (inheritance + pack union).

    Resolution rules:

    * The template's own declared posture is authoritative (child-wins).
    * If the template declares none but ``extends`` a parent, the parent's
      effective posture is inherited.
    * Each pack in ``uses_packs`` unions its effective posture on top
      (flags OR to the more-capable value), so packs contribute additively.

    Args:
        template: The parsed (pass-1) template.
        load_pack: Resolves a pack name to its ``CompanyTemplate``.
        load_parent: Resolves a parent template name to its
            ``CompanyTemplate``.
        strategy: Posture-expansion strategy (default ``"named"``).

    Returns:
        The effective :class:`PostureConfig`, or ``None`` when neither the
        template, its parent, nor any pack declares a posture.
    """
    expander = strategy if strategy is not None else get_posture_strategy()

    host: PostureConfig | None = (
        expander.expand(template.posture) if template.posture is not None else None
    )
    if host is None and template.extends is not None:
        host = resolve_template_posture(
            load_parent(template.extends),
            load_pack=load_pack,
            load_parent=load_parent,
            strategy=expander,
        )

    pack_union: PostureConfig | None = None
    for pack_name in template.uses_packs:
        pack_posture = resolve_template_posture(
            load_pack(pack_name),
            load_pack=load_pack,
            load_parent=load_parent,
            strategy=expander,
        )
        if pack_posture is not None:
            pack_union = (
                pack_posture if pack_union is None else pack_union.merge(pack_posture)
            )

    if pack_union is None:
        return host
    return (host or PostureConfig()).merge(pack_union)
