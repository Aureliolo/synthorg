"""Ontology feature state slice.

Holds the semantic-ontology service plus its optional drift collaborators
(the drift-report store, the drift-detection service, and the org-memory
sync). The service raises 503 when unwired; the drift collaborators are
optional and read defensively.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.ontology.drift.service import (
    DriftDetectionService,  # noqa: TC001
)
from synthorg.ontology.service import OntologyService  # noqa: TC001
from synthorg.ontology.sync import OntologyOrgMemorySync  # noqa: TC001
from synthorg.persistence.ontology_protocol import (
    OntologyDriftReportRepository,  # noqa: TC001
)


class OntologyStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the ontology feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: OntologyService | None = None
    drift_report_store: OntologyDriftReportRepository | None = None
    drift_detection_service: DriftDetectionService | None = None
    sync_service: OntologyOrgMemorySync | None = None
