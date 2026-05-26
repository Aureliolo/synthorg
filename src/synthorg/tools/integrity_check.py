"""Tool registry integrity checker.

Computes SHA-256 hashes of ``ToolDefinition`` objects at boot time
and compares them against recorded hashes from a previous boot.
Hash mismatches indicate that tool definitions have been modified
since the last recorded state -- potentially indicating supply-chain
tampering or unintentional drift.

The checker is invoked from ``tools/factory.py`` during company
startup.
"""

import copy
import hashlib
import json
from collections.abc import Mapping  # noqa: TC003
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from types import MappingProxyType
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_REGISTRY_INTEGRITY_CHECK_COMPLETE,
    TOOL_REGISTRY_INTEGRITY_CHECK_START,
    TOOL_REGISTRY_INTEGRITY_VIOLATION,
)

logger = get_logger(__name__)


class ToolIntegrityCheckConfig(BaseModel):
    """Configuration for tool registry integrity checking.

    Attributes:
        enabled: Whether integrity checking is active at boot.
        hashes_file: Path to a JSON file with prior recorded hashes.
            When ``None``, no comparison is performed (first boot).
        fail_on_violation: If ``True``, raise on hash mismatch.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether integrity checking is active at boot",
    )
    hashes_file: Path | None = Field(
        default=None,
        description="Path to JSON file with prior recorded hashes",
    )
    fail_on_violation: bool = Field(
        default=False,
        description="Raise on hash mismatch if True",
    )


class ToolIntegrityViolation(BaseModel):
    """A single tool definition hash mismatch.

    Attributes:
        tool_name: Name of the tool with a hash mismatch.
        expected_hash: Hash from prior recorded state.
        actual_hash: Hash computed at current boot.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tool_name: NotBlankStr = Field(description="Tool with mismatch")
    expected_hash: NotBlankStr = Field(description="Prior recorded hash")
    actual_hash: NotBlankStr = Field(description="Current computed hash")


class ToolIntegrityReport(BaseModel):
    """Result of a tool registry integrity check.

    Attributes:
        violations: Tuple of detected hash mismatches.
        current_hashes: Map of tool name to current SHA-256 hash.
        checked_at: UTC timestamp of the check.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    violations: tuple[ToolIntegrityViolation, ...] = Field(
        default=(),
        description="Detected hash mismatches",
    )
    current_hashes: Mapping[str, str] = Field(
        default_factory=dict,
        description="Tool name to current SHA-256 hash",
    )
    checked_at: datetime = Field(description="UTC timestamp of the check")

    @model_validator(mode="after")
    def _deep_copy_hashes(self) -> Self:
        """Deep-copy and freeze current_hashes.

        Returns:
            Result of type ``Self``.
        """
        object.__setattr__(
            self,
            "current_hashes",
            MappingProxyType(copy.deepcopy(dict(self.current_hashes))),
        )
        return self


def compute_tool_hash(tool_def: Any) -> str:
    """Compute a deterministic SHA-256 hash of a ToolDefinition.

    Serializes the tool's ``name``, ``description``, and
    ``parameters_schema`` to sorted JSON and returns the hex digest.

    Args:
        tool_def: A ``ToolDefinition`` instance.

    Returns:
        64-character hex SHA-256 digest.
    """
    canonical = json.dumps(
        {
            "name": tool_def.name,
            "description": tool_def.description,
            "parameters_schema": tool_def.parameters_schema,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolIntegrityChecker:
    """Check tool definitions against previously recorded hashes.

    Args:
        prior_hashes: Map of tool name to expected SHA-256 hash
            from a previous boot.  When ``None``, the check records
            current hashes without comparing.
    """

    def __init__(
        self,
        prior_hashes: Mapping[str, str] | None = None,
        *,
        fail_on_violation: bool = False,
    ) -> None:
        self._prior = dict(prior_hashes) if prior_hashes else {}
        self._fail_on_violation = fail_on_violation

    def check(self, tools: tuple[Any, ...]) -> ToolIntegrityReport:
        """Check tool definitions and return an integrity report.

        Args:
            tools: Tuple of ``BaseTool`` instances to check.

        Returns:
            Report with any violations and all current hashes.

        Raises:
            RuntimeError: If the operation fails at runtime.
        """
        logger.debug(
            TOOL_REGISTRY_INTEGRITY_CHECK_START,
            tool_count=len(tools),
            prior_hash_count=len(self._prior),
        )

        current_hashes: dict[str, str] = {}
        violations: list[ToolIntegrityViolation] = []

        for tool in tools:
            definition = tool.to_definition()
            name = definition.name
            current_hash = compute_tool_hash(definition)
            current_hashes[name] = current_hash

            if name in self._prior and self._prior[name] != current_hash:
                violation = ToolIntegrityViolation(
                    tool_name=name,
                    expected_hash=self._prior[name],
                    actual_hash=current_hash,
                )
                violations.append(violation)
                logger.error(
                    TOOL_REGISTRY_INTEGRITY_VIOLATION,
                    tool_name=name,
                    expected_hash=self._prior[name],
                    actual_hash=current_hash,
                )

        report = ToolIntegrityReport(
            violations=tuple(violations),
            current_hashes=current_hashes,
            checked_at=datetime.now(UTC),
        )

        if violations and self._fail_on_violation:
            msg = f"Tool registry integrity violated: {len(violations)} tool(s) changed"
            raise RuntimeError(msg)

        logger.debug(
            TOOL_REGISTRY_INTEGRITY_CHECK_COMPLETE,
            checked_count=len(tools),
            violation_count=len(violations),
        )

        return report
