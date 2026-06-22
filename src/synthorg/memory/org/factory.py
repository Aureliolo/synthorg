# module-kind: feature
"""Factory for the hybrid org-memory backend.

Builds the :class:`HybridPromptRetrievalBackend` from the operator's
``OrgMemoryConfig`` and the shared persistence-backed org-fact store, so
the boot wiring can publish one live backend that every consumer (HR
promotion / offboarding snapshots, the ontology admin sync, the
knowledge-architect tools) resolves instead of receiving ``None``.
"""

from synthorg.memory.org.config import OrgMemoryConfig
from synthorg.memory.org.hybrid_backend import HybridPromptRetrievalBackend
from synthorg.persistence.memory_protocol import OrgFactRepository


def build_org_memory_backend(
    config: OrgMemoryConfig,
    org_fact_repo: OrgFactRepository,
) -> HybridPromptRetrievalBackend:
    """Build the hybrid org-memory backend from config + the shared store.

    Args:
        config: Operator org-memory configuration (core policies, write
            access control).
        org_fact_repo: The persistence-backed extended-fact store
            (``persistence.org_facts``); its connection lifecycle is owned
            by the shared persistence backend, not the returned backend.

    Returns:
        A constructed (not yet connected) :class:`HybridPromptRetrievalBackend`.
        The caller awaits ``connect()`` before publishing it.
    """
    return HybridPromptRetrievalBackend(
        core_policies=config.core_policies,
        store=org_fact_repo,
        access_config=config.write_access,
    )
