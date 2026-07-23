"""Publish-strategy factory keyed on the resolved publish method.

Resolves an ``auto`` method from the call's inputs, then selects the concrete
strategy via a :class:`~synthorg.core.registry.StrategyRegistry`. Adding a
method is a registry entry plus a strategy; the tool does not change.
"""

from synthorg.core.registry import StrategyRegistry
from synthorg.integrations.connections.registry_target import PublishMethod
from synthorg.tools.publish.errors import PublishToolArgumentError
from synthorg.tools.publish.strategies.digest_promote import DigestPromoteStrategy
from synthorg.tools.publish.strategies.protocol import PublishStrategy
from synthorg.tools.publish.strategies.workspace_push import WorkspacePushStrategy

_REGISTRY: StrategyRegistry[PublishStrategy] = StrategyRegistry(
    {
        PublishMethod.WORKSPACE_PUSH: WorkspacePushStrategy,
        PublishMethod.DIGEST_PROMOTE: DigestPromoteStrategy,
    },
    kind="publish_strategy",
)


def resolve_publish_method(
    effective: PublishMethod, *, has_digest: bool, has_image_path: bool
) -> PublishMethod:
    """Resolve ``auto`` to a concrete method from the call's inputs.

    Args:
        effective: The method after merging the call's method with the
            target's default (``auto`` when neither pins one).
        has_digest: Whether a source digest was supplied.
        has_image_path: Whether a workspace image path was supplied.

    Returns:
        The concrete :class:`PublishMethod` to use.

    Raises:
        PublishToolArgumentError: ``auto`` cannot be resolved because the
            inputs are ambiguous (both) or absent (neither).
    """
    if effective is not PublishMethod.AUTO:
        return effective
    if has_image_path and not has_digest:
        return PublishMethod.WORKSPACE_PUSH
    if has_digest and not has_image_path:
        return PublishMethod.DIGEST_PROMOTE
    if has_digest and has_image_path:
        msg = (
            "both a source digest and an image path were given; set method to "
            "'digest_promote' or 'workspace_push' to choose"
        )
        raise PublishToolArgumentError(msg)
    msg = "a source digest or an image path is required to push"
    raise PublishToolArgumentError(msg)


def build_publish_strategy(method: PublishMethod) -> PublishStrategy:
    """Build the strategy for a concrete (non-``auto``) publish method.

    Args:
        method: A concrete publish method.

    Returns:
        The strategy instance.

    Raises:
        StrategyFactoryNotFoundError: The method has no wired strategy
            (``auto`` must be resolved first via :func:`resolve_publish_method`).
    """
    return _REGISTRY.build(method)


__all__ = ["build_publish_strategy", "resolve_publish_method"]
