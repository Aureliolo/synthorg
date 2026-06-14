"""Automated reporting event constants."""

from typing import Final

REPORTING_GENERATION_STARTED: Final[str] = "reporting.generation.started"
REPORTING_GENERATION_COMPLETED: Final[str] = "reporting.generation.completed"
REPORTING_GENERATION_FAILED: Final[str] = "reporting.generation.failed"
REPORTING_SERVICE_CREATED: Final[str] = "reporting.service.created"
REPORTING_PERIOD_COMPUTED: Final[str] = "reporting.period.computed"

# MCP ReportsService.
REPORT_GENERATED: Final[str] = "reporting.report.generated"
REPORT_LISTED: Final[str] = "reporting.report.listed"
