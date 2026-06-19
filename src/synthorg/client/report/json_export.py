"""JSON archival export report strategy."""

from typing import Final

from pydantic import JsonValue

from synthorg.client.models import SimulationMetrics
from synthorg.core.iso_datetime import now_iso_utc

_SCHEMA_VERSION: Final[str] = "1.0"


class JsonExportReport:
    """Archival JSON export with schema metadata.

    Designed for long-term storage or offline analytics. Wraps the
    metrics dump in a schema envelope that identifies the format
    version and the export timestamp so consumers can evolve safely.
    """

    async def generate_report(
        self,
        metrics: SimulationMetrics,
    ) -> dict[str, JsonValue]:
        """Return metrics wrapped in an archival envelope."""
        return {
            "format": "json_export",
            "schema_version": _SCHEMA_VERSION,
            "exported_at": now_iso_utc(),
            "metrics": metrics.model_dump(mode="json"),
        }
