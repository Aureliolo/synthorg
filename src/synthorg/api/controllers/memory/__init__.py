"""Memory admin REST controllers, decomposed per sub-domain.

Each sub-controller (fine-tune, checkpoints, entries, embedder) is
registered individually through the memory feature manifest
(``synthorg.memory.feature``); this package exposes no aggregate
re-export so registration flows only through the manifest. All
sub-controllers carry the CEO / SYSTEM role guard.
"""
