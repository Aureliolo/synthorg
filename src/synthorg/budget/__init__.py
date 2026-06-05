"""Budget and cost tracking domain models.

This package uses explicit per-module imports rather than re-exporting
everything from the top level. Import specific symbols from their
defining submodule, e.g.::

    from synthorg.budget.call_category import LLMCallCategory
    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.tracker import CostTracker

Eagerly re-exporting here pulled ``budget.risk_record`` (-> security ->
engine -> ...) onto the import path of every importer of a budget *leaf*.
The worst case was ``providers.cost_recording`` importing
``LLMCallCategory`` (defined in ``budget.call_category``): that leaf
import ran this init, whose cascade reached back through security/engine
into the partially-initialised ``providers.cost_recording``, closing the
cold-import cycle. Keeping this init empty stops the cascade.
"""
