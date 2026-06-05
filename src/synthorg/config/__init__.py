"""Configuration loading, validation, and schema models.

This package uses explicit per-module imports rather than re-exporting
everything from the top level. Import specific symbols from their
defining submodule, e.g.::

    from synthorg.config.loader import load_config, bootstrap_logging
    from synthorg.config.schema import RootConfig
    from synthorg.config.provider_schema import ProviderConfig
    from synthorg.config.errors import ConfigError

Eagerly re-exporting ``RootConfig`` / ``load_config`` here forced every
importer of a config *leaf* (e.g. ``config.provider_schema``, a light
provider/model schema) to load ``config.schema`` (the aggregating root
config, which imports every subsystem config). That made the persistence
layer's DTO imports drag the whole graph and re-introduced a cold-import
cycle. Keeping this init empty stops that.
"""
