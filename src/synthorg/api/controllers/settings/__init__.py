"""Settings REST controllers, decomposed per sub-domain.

Each sub-controller (core CRUD + schema, observability sinks, security
config export/import) is registered individually through the settings
feature manifest (``synthorg.settings.feature``); this package exposes no
aggregate re-export so registration flows only through the manifest.
"""
