# module-kind: feature
"""Factory for the ontology drift-detection service.

Selects the configured detection strategy (passive keyword-overlap,
active on-demand validation, or the layered tier-split that runs active
for CORE entities and passive for USER entities) and assembles a
:class:`DriftDetectionService` over it. Keeps the strategy discriminator
+ collaborator wiring in one place so the boot path stays a thin call.

Returns ``None`` (the subsystem stays off, its controller 503s) when
drift detection is disabled (``strategy: none``) or no memory backend is
available -- every active strategy reads agent memories, so without a
memory backend there is nothing to sample.
"""

from synthorg.memory.protocol import MemoryBackend
from synthorg.ontology.config import DriftDetectionConfig, DriftStrategy
from synthorg.ontology.drift.active import ActiveValidatorStrategy
from synthorg.ontology.drift.layered import LayeredDetectionStrategy
from synthorg.ontology.drift.passive import PassiveMonitorStrategy
from synthorg.ontology.drift.protocol import DriftDetectionStrategy
from synthorg.ontology.drift.service import DriftDetectionService
from synthorg.persistence.ontology_protocol import (
    OntologyDriftReportRepository,
    OntologyEntityRepository,
)


def build_drift_detection_service(
    *,
    ontology: OntologyEntityRepository,
    memory: MemoryBackend | None,
    config: DriftDetectionConfig,
    store: OntologyDriftReportRepository | None = None,
) -> DriftDetectionService | None:
    """Build the drift-detection service for the configured strategy.

    Args:
        ontology: Ontology entity repository for definitions + tiers.
        memory: Agent-memory backend the strategies sample; ``None``
            disables drift detection (returns ``None``).
        config: Drift detection configuration.
        store: Optional report store for persistence.

    Returns:
        A constructed ``DriftDetectionService``, or ``None`` when drift
        detection is disabled or no memory backend is available.
    """
    if config.strategy is DriftStrategy.NONE or memory is None:
        return None

    strategy: DriftDetectionStrategy
    if config.strategy is DriftStrategy.LAYERED:
        strategy = LayeredDetectionStrategy(
            ontology=ontology,
            core_strategy=ActiveValidatorStrategy(
                ontology=ontology,
                memory=memory,
                threshold=config.threshold,
            ),
            user_strategy=PassiveMonitorStrategy(
                ontology=ontology,
                memory=memory,
                threshold=config.threshold,
            ),
        )
    elif config.strategy is DriftStrategy.ACTIVE:
        strategy = ActiveValidatorStrategy(
            ontology=ontology,
            memory=memory,
            threshold=config.threshold,
        )
    else:
        strategy = PassiveMonitorStrategy(
            ontology=ontology,
            memory=memory,
            threshold=config.threshold,
        )

    return DriftDetectionService(
        strategy=strategy,
        ontology=ontology,
        config=config,
        store=store,
    )


__all__ = ["build_drift_detection_service"]
