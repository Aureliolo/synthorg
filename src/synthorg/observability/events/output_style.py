"""Output-style policy event constants."""

from typing import Final

OUTPUT_STYLE_PACK_LOADED: Final[str] = "output_style.pack.loaded"
OUTPUT_STYLE_PACK_NOT_FOUND: Final[str] = "output_style.pack.not_found"
OUTPUT_STYLE_PACK_INVALID: Final[str] = "output_style.pack.invalid"
OUTPUT_STYLE_CONFIG_VALIDATED: Final[str] = "output_style.config.validated"
OUTPUT_STYLE_FINDINGS_TRUNCATED: Final[str] = "output_style.findings.truncated"
OUTPUT_STYLE_VIOLATION_REWRITTEN: Final[str] = "output_style.violation.rewritten"
OUTPUT_STYLE_VIOLATION_SHADOWED: Final[str] = "output_style.violation.shadowed"
OUTPUT_STYLE_EXEMPTION_GRANTED: Final[str] = "output_style.exemption.granted"
OUTPUT_STYLE_EXEMPTION_REQUESTED: Final[str] = "output_style.exemption.requested"
OUTPUT_STYLE_PROMPT_INJECTED: Final[str] = "output_style.prompt.injected"
OUTPUT_STYLE_GATE_REJECTED: Final[str] = "output_style.gate.rejected"
OUTPUT_STYLE_GATE_PASSED: Final[str] = "output_style.gate.passed"
OUTPUT_STYLE_SERVICE_REBUILT: Final[str] = "output_style.service.rebuilt"
OUTPUT_STYLE_SNAPSHOT_REFRESHED: Final[str] = "output_style.snapshot.refreshed"
