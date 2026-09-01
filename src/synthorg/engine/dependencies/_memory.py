# module-kind: declarative
"""What an agent recalls, and what it learns."""

from dataclasses import dataclass

from synthorg.memory.injection import MemoryInjectionStrategyProvider
from synthorg.memory.procedural.capture.protocol import CaptureStrategy
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.protocol import MemoryBackend
from synthorg.ontology.injection.protocol import OntologyInjectionStrategy


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineMemory:
    """The read side and the write side of durable recall.

    Attributes:
        memory_backend: Where memories live, or ``None`` when
            ``memory.embedder_model`` is unset and memory stays off.
        memory_injection_strategy_provider: Resolved per unit of work
            rather than captured, because memory can be wired after the
            engine is built and a captured ``None`` would leave every
            agent with no recall until the process restarted.
        ontology_injection_strategy: Ontology context and its tool, or
            ``None``.
        procedural_memory_config: What the failure-driven proposer is
            configured with, or ``None`` to build no proposer.
        capture_strategy: How a success is captured, or ``None``.
        distillation_capture_enabled: The boot fallback for the capture
            switch; the engine re-reads the live value per task.
    """

    memory_backend: MemoryBackend | None
    memory_injection_strategy_provider: MemoryInjectionStrategyProvider | None
    ontology_injection_strategy: OntologyInjectionStrategy | None
    procedural_memory_config: ProceduralMemoryConfig | None
    capture_strategy: CaptureStrategy | None
    distillation_capture_enabled: bool


__all__ = ["EngineMemory"]
