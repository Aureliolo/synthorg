"""Provider management: runtime CRUD for LLM providers.

This package uses explicit per-module imports rather than re-exporting
from the top level. Import specific symbols from their defining
submodule, e.g.::

    from synthorg.providers.management.service import ProviderManagementService
    from synthorg.providers.management.capability_dtos import PresetOverride

Eagerly re-exporting ``ProviderManagementService`` here pulled
``config.schema`` (the aggregating root config) onto the import path of
the persistence layer (``persistence.protocol`` references the preset and
audit DTOs defined in :mod:`capability_dtos`), which re-introduced a
cold-import cycle. Keeping this init empty stops that.
"""
