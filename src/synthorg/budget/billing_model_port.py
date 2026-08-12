"""Where the ledger asks how a provider charges.

One owner decides a connection's billing model: the connection's own
configuration. The ledger reads it through this port rather than importing the
provider registry, so the question has a single answer and a single place that
answers it, and a test can supply a map without standing up a registry.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.billing_enums import BillingModel


@runtime_checkable
class BillingModelResolver(Protocol):
    """Answers how a named provider connection charges."""

    def billing_model_for(self, provider: str) -> BillingModel:
        """Return the billing model declared for *provider*.

        Args:
            provider: The provider connection name a cost record names.

        Returns:
            The declared billing model, or :attr:`BillingModel.UNKNOWN` for a
            connection this resolver cannot answer for. UNKNOWN rather than a
            per-token assumption: a ceiling assumed to bind when it may not is
            the failure this whole seam exists to remove.
        """
        ...
