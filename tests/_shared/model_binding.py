"""Test helpers for the explicit ``(provider, model)`` binding.

Every LLM dispatch names both halves: a provider is a registered *connection*,
so a bare model id names no dispatch target. Tests therefore need two things
constantly -- the canonical ``MODEL_REF`` string for a bound pair, and a
``ConnectionSelector`` that serves a double under that provider name. Both
live here so a test does not hand-roll either.
"""

from collections.abc import Mapping
from typing import Final
from unittest.mock import AsyncMock

from synthorg.providers.protocol import CompletionProvider, ConnectionSelector
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared.mock_of import mock_of

#: The provider name test doubles register under, matching the vendor-neutral
#: names the rest of the suite uses.
TEST_PROVIDER: Final[str] = "test-provider"

#: The model id test doubles answer to. Opaque to a scripted driver.
TEST_MODEL_ID: Final[str] = "example-capable-001"


def bound_ref(
    model_id: str = TEST_MODEL_ID,
    *,
    provider: str = TEST_PROVIDER,
) -> str:
    """Return the canonical ``MODEL_REF`` value for a bound pair.

    Args:
        model_id: The model id half of the pair.
        provider: The connection half of the pair.

    Returns:
        The stored form a ``SettingType.MODEL_REF`` setting holds.
    """
    return serialize_model_ref(ModelRef(provider=provider, model_id=model_id))


def bound_model(
    model_id: str = TEST_MODEL_ID,
    *,
    provider: str = TEST_PROVIDER,
) -> ModelRef:
    """Return a bound :class:`ModelRef` for a test dispatch target.

    Args:
        model_id: The model id half of the pair.
        provider: The connection half of the pair.

    Returns:
        The bound reference.
    """
    return ModelRef(provider=provider, model_id=model_id)


def one_connection(
    provider: CompletionProvider,
    *,
    name: str = TEST_PROVIDER,
) -> ConnectionSelector:
    """Return a selector serving *provider* under the connection *name*.

    Args:
        provider: The completion double to serve.
        name: The connection name the selector answers to.

    Returns:
        A ``ConnectionSelector`` that raises ``KeyError`` for any other name,
        so a test that dispatches on an unexpected connection fails loudly
        instead of silently reusing the one double.
    """
    return connections({name: provider})


def connections(
    by_name: Mapping[str, CompletionProvider],
) -> ConnectionSelector:
    """Return a selector over *by_name*.

    Args:
        by_name: Completion doubles keyed by connection name.

    Returns:
        A ``ConnectionSelector`` reading *by_name*, raising ``KeyError`` for
        an unregistered connection.
    """

    def _select(name: str) -> CompletionProvider:
        return by_name[name]

    return _select


def model_ref_resolver(
    refs: Mapping[tuple[str, str], str] | None = None,
    *,
    default: str = bound_ref(),
) -> ConfigResolverProtocol:
    """Return a resolver whose ``get_str`` serves fixed ``MODEL_REF`` values.

    Most consumers now re-read their own pair per call, so a unit test that
    only cares about the dispatch needs a resolver that answers with a bound
    pair and nothing else.

    Args:
        refs: Stored values keyed by ``(namespace, key)``.
        default: Value served for an unlisted key; the default is a bound
            pair, so a test opts in to "unset" by passing ``default=""``.

    Returns:
        A ``ConfigResolverProtocol`` double.
    """
    table = dict(refs or {})

    async def _get_str(namespace: str, key: str) -> str:
        return table.get((namespace, key), default)

    resolver: ConfigResolverProtocol = mock_of[ConfigResolverProtocol](
        get_str=AsyncMock(side_effect=_get_str),
    )
    return resolver
