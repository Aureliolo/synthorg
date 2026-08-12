# module-kind: code
"""A point-in-time answer to "how does this connection charge".

The ledger asks the question once per recorded call, synchronously, inside
``CostTracker.record``. The answer lives on the persisted ``ProviderConfig``
and is read through an async resolver, so the ledger reads a snapshot rebuilt
whenever the provider set is, rather than reaching for the config store on a
path that cannot await.

That keeps one owner for the declaration (the connection's own config) while
letting the ledger stamp it on the row where it belongs.
"""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.billing_enums import BillingModel


class ProviderBillingModelSnapshot:
    """Answers how each configured provider connection charges.

    Args:
        configs: The provider set this snapshot was built from.
    """

    __slots__ = ("_by_provider",)

    def __init__(self, configs: Mapping[str, ProviderConfig]) -> None:
        self._by_provider: Mapping[str, BillingModel] = MappingProxyType(
            {name: config.billing_model for name, config in configs.items()}
        )

    def billing_model_for(self, provider: str) -> BillingModel:
        """Return the billing model declared for *provider*.

        Args:
            provider: The provider connection name a cost record names.

        Returns:
            The declared billing model, or :attr:`BillingModel.UNKNOWN` for a
            connection this snapshot does not hold: one removed since the call,
            or one recorded under a label that is not a configured connection.
            UNKNOWN rather than a per-token assumption, because a ceiling
            assumed to bind when it may not is the failure being fixed.
        """
        return self._by_provider.get(provider, BillingModel.UNKNOWN)
