"""Budget and cost tracking domain models.

This package uses explicit per-module imports rather than re-exporting
everything from the top level. Import specific symbols from their
defining submodule, e.g.::

    from synthorg.budget.call_category import LLMCallCategory
    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.tracker import CostTracker

Eagerly re-exporting here pulled ``budget.risk_record`` (-> security ->
engine -> ...) onto the import path of every importer of a budget *leaf*
(most notably ``providers.cost_recording``, which imports the
``LLMCallCategory`` enum). That cascade reached back into the
partially-initialised ``providers.cost_recording`` module and was the
spine of the cold-import cycle. Keeping this init empty stops it.
"""
