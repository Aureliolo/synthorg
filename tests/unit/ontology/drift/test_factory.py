"""Tests for the drift-detection service factory."""

import pytest

from synthorg.memory.protocol import MemoryBackend
from synthorg.ontology.config import DriftDetectionConfig, DriftStrategy
from synthorg.ontology.drift.factory import build_drift_detection_service
from synthorg.persistence.ontology_protocol import OntologyEntityRepository
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def test_none_strategy_returns_none() -> None:
    ontology: OntologyEntityRepository = mock_of[OntologyEntityRepository]()
    memory: MemoryBackend = mock_of[MemoryBackend]()
    service = build_drift_detection_service(
        ontology=ontology,
        memory=memory,
        config=DriftDetectionConfig(strategy=DriftStrategy.NONE),
    )
    assert service is None


def test_missing_memory_returns_none() -> None:
    ontology: OntologyEntityRepository = mock_of[OntologyEntityRepository]()
    service = build_drift_detection_service(
        ontology=ontology,
        memory=None,
        config=DriftDetectionConfig(strategy=DriftStrategy.PASSIVE),
    )
    assert service is None


@pytest.mark.parametrize(
    ("strategy", "expected_name"),
    [
        (DriftStrategy.PASSIVE, "passive"),
        (DriftStrategy.ACTIVE, "active"),
        (DriftStrategy.LAYERED, "layered"),
    ],
)
def test_strategy_selection(strategy: DriftStrategy, expected_name: str) -> None:
    ontology: OntologyEntityRepository = mock_of[OntologyEntityRepository]()
    memory: MemoryBackend = mock_of[MemoryBackend]()
    service = build_drift_detection_service(
        ontology=ontology,
        memory=memory,
        config=DriftDetectionConfig(strategy=strategy),
    )
    assert service is not None
    assert service.strategy_name == expected_name
