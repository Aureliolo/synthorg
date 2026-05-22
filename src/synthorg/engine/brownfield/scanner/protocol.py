"""Structure-map scanner protocol and partial-contribution model."""

from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.codebase_structure_map import (  # noqa: TC001 -- Pydantic fields
    BuildFile,
    Dependency,
    Ecosystem,
    EntryPoint,
    Module,
    TestSuite,
)


class EcosystemScan(BaseModel):
    """One scanner's partial contribution to a structure map.

    The aggregator merges the contributions of every matching scanner into
    a single :class:`~synthorg.core.codebase_structure_map.CodebaseStructureMap`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    modules: tuple[Module, ...] = Field(default=())
    entry_points: tuple[EntryPoint, ...] = Field(default=())
    test_suites: tuple[TestSuite, ...] = Field(default=())
    build_files: tuple[BuildFile, ...] = Field(default=())
    dependencies: tuple[Dependency, ...] = Field(default=())


@runtime_checkable
class StructureMapScanner(Protocol):
    """Deterministic, per-ecosystem reader of codebase structure.

    Implementations are pure (no LLM, no network): given a workspace path
    they read manifests and the file tree and return the facts they can
    establish for their ecosystem.
    """

    def ecosystem(self) -> Ecosystem:
        """Return the ecosystem discriminator this scanner handles."""
        ...

    def detect(self, workspace_path: Path) -> bool:
        """Return ``True`` if this ecosystem is present under *workspace_path*."""
        ...

    def scan(self, workspace_path: Path) -> EcosystemScan:
        """Read *workspace_path* and return this ecosystem's contribution."""
        ...
