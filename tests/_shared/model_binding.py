"""Test helpers for the explicit ``(provider, model)`` binding.

Every LLM dispatch names both halves: a provider is a registered *connection*,
so a bare model id names no dispatch target. Tests therefore need two things
constantly -- the canonical ``MODEL_REF`` string for a bound pair, and a
``ConnectionSelector`` that serves a double under that provider name. Both
live here so a test does not hand-roll either.
"""

from collections.abc import Mapping, Sequence
from typing import Final
from unittest.mock import AsyncMock

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.hr.hire_model_proposal import ProviderCatalogue
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


#: Price step between the models a test catalogue offers, so a cost-biased
#: selection has something to bias ON. Identically-priced models score
#: identically, and every spend profile then lands on the same one.
_CATALOGUE_COST_STEP: Final[float] = 1.0

#: Context step, paid for by the price step. Cost alone is not a spread: with
#: every model otherwise identical the cheapest dominates outright and no
#: profile can prefer anything else, so a catalogue meant to offer a choice
#: has to make the dearer models actually better at something.
_CATALOGUE_CONTEXT_STEP: Final[int] = 100_000


def provider_catalogue(
    models: Sequence[str] = (TEST_MODEL_ID,),
    *,
    provider: str = TEST_PROVIDER,
) -> ProviderCatalogue:
    """Return a catalogue serving one provider carrying *models*.

    The proposal path scores a candidate against the operator's configured
    models, so a test that expects a hire to be bindable has to give it
    something to bind to. Each model costs more than the one before it, in the
    order given, so a catalogue of several offers a genuine cost-to-capability
    spread rather than N indistinguishable rows.

    Args:
        models: Model ids the provider offers, cheapest first.
        provider: The connection name they sit under.

    Returns:
        A ``ProviderCatalogue`` double.
    """
    configs = {
        provider: ProviderConfig(
            # API-key auth is the default and requires a catalog entry to
            # resolve its credentials from; the proposal never dispatches, but
            # the config still has to be a valid one.
            connection_name=NotBlankStr(provider),
            models=tuple(
                ProviderModelConfig(
                    id=NotBlankStr(model_id),
                    cost_per_1k_input=(index + 1) * _CATALOGUE_COST_STEP,
                    cost_per_1k_output=(index + 1) * _CATALOGUE_COST_STEP,
                    max_context=(index + 1) * _CATALOGUE_CONTEXT_STEP,
                )
                for index, model_id in enumerate(models)
            ),
        )
    }
    return _catalogue_of(configs)


def no_provider_catalogue() -> ProviderCatalogue:
    """Return a catalogue for an org that has configured no provider at all.

    Distinct from a provider carrying no models: one is "you have not
    connected anything", the other is "nothing you connected fits", and the
    proposal says different things about them.

    Returns:
        A ``ProviderCatalogue`` double serving nothing.
    """
    return _catalogue_of({})


def _catalogue_of(configs: Mapping[str, ProviderConfig]) -> ProviderCatalogue:
    async def _list() -> Mapping[str, ProviderConfig]:
        return configs

    catalogue: ProviderCatalogue = mock_of[ProviderCatalogue](
        list_providers=AsyncMock(side_effect=_list),
    )
    return catalogue
