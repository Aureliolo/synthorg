"""Company-template domain enumerations."""

from enum import StrEnum


class SkillPattern(StrEnum):
    """Skill interaction patterns for company templates.

    Based on the five-pattern taxonomy: Tool Wrapper, Generator,
    Reviewer, Inversion, and Pipeline.

    Attributes:
        TOOL_WRAPPER: On-demand domain expertise; agents
            self-direct using specialized context.
        GENERATOR: Consistent structured output from reusable
            templates.
        REVIEWER: Modular rubric-based evaluation; separates
            what to check from how to check it.
        INVERSION: Agent interviews user before acting;
            structured requirements gathering.
        PIPELINE: Strict sequential workflow with hard
            checkpoints between stages.
    """

    TOOL_WRAPPER = "tool_wrapper"
    GENERATOR = "generator"
    REVIEWER = "reviewer"
    INVERSION = "inversion"
    PIPELINE = "pipeline"


class PostureName(StrEnum):
    """Named operating postures a template can declare.

    A posture expands to a coherent runtime feature-flag bundle (knowledge
    substrate, conversational chat modes, red-team gate, mid-flight steering,
    cost-dial) so a template configures behaviour, not just an org chart. The
    toolsmith is intentionally excluded: enabling it needs an explicit
    capability allowlist and stays an operator opt-in.

    Attributes:
        AUTONOMOUS: High-autonomy delivery; steering and knowledge on,
            human chat off.
        SUPERVISED_CLIENT_FACING: Human-in-the-loop client work; group chat
            and agent invite on for stakeholder collaboration.
        KNOWLEDGE_HEAVY: Knowledge-substrate-grounded work; entailment
            grounding and a shared knowledge base.
        COST_DISCIPLINED: Budget-first operation; auto-downgrade on, optional
            features off to minimise spend.
        SECURITY_HARDENED: Security-first operation; red-team completion gate
            on at a lowered stakes floor, self-extension off.
        RESEARCH_AUTONOMOUS: Autonomous inquiry; knowledge substrate, steering,
            and clarify-or-park + routing proposals on.
    """

    AUTONOMOUS = "autonomous"
    SUPERVISED_CLIENT_FACING = "supervised_client_facing"
    KNOWLEDGE_HEAVY = "knowledge_heavy"
    COST_DISCIPLINED = "cost_disciplined"
    SECURITY_HARDENED = "security_hardened"
    RESEARCH_AUTONOMOUS = "research_autonomous"
