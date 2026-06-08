"""Living-documentation engine enumerations."""

from enum import StrEnum


class DocType(StrEnum):
    """Living-document type taxonomy.

    ``STATUS_REPORT`` records progress/decisions; ``DELIVERABLE`` is an
    iteratively edited artifact (PRD, design, research memo);
    ``KNOWLEDGE_NOTE`` is freeform knowledge; ``CODEBASE_ANALYSIS`` is a
    brownfield-intake architecture/health assessment; ``RUN_NARRATIVE``
    is the Chief-of-Staff's account of a completed brief from the brain
    and flight recorder. The type drives only wiki filtering/rendering.
    """

    STATUS_REPORT = "status_report"
    DELIVERABLE = "deliverable"
    KNOWLEDGE_NOTE = "knowledge_note"
    CODEBASE_ANALYSIS = "codebase_analysis"
    RUN_NARRATIVE = "run_narrative"
