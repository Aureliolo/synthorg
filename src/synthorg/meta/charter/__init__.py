"""Deep CEO interview to project charter subsystem.

A structured requirements-elicitation interview over the Chief of Staff
conversation substrate produces a reviewable :class:`ProjectCharter`
that, on approval, drives a real project run through the work pipeline
spine.
"""

from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.dispatch import CharterDispatcher
from synthorg.meta.charter.factory import build_charter_interview_strategy
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    CharterApprovalResult,
    CharterDraft,
    CharterEditArgs,
    InterviewDecision,
    InterviewTurnArgs,
    InterviewTurnResult,
    ProjectCharter,
    ScopeBoundaries,
)
from synthorg.meta.charter.service import CharterInterviewService
from synthorg.meta.charter.strategy import (
    CharterInterviewStrategy,
    LLMCharterInterviewer,
)

__all__ = [
    "BudgetEnvelope",
    "CharterApprovalResult",
    "CharterConfig",
    "CharterDispatcher",
    "CharterDraft",
    "CharterEditArgs",
    "CharterInterviewService",
    "CharterInterviewStrategy",
    "InterviewDecision",
    "InterviewTurnArgs",
    "InterviewTurnResult",
    "LLMCharterInterviewer",
    "ProjectCharter",
    "ScopeBoundaries",
    "build_charter_interview_strategy",
]
