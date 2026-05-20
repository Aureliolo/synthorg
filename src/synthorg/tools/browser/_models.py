"""Response models for the headless browser tool.

Every model is frozen Pydantic v2 with ``extra='forbid'`` and
``allow_inf_nan=False``. Tool handlers ``model_dump()`` these into the
``ToolExecutionResult.metadata`` mapping; the JSON string of the dump
is also placed into ``content`` so the LLM-facing surface remains
plain text.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.tools.browser._constants import SHA256_HEX_PATTERN

_RESPONSE_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


class A11yViolation(BaseModel):
    """A single accessibility violation reported by axe-core."""

    model_config = _RESPONSE_CONFIG

    rule_id: NotBlankStr = Field(
        description="axe-core rule id (e.g. 'button-name').",
    )
    impact: Literal["minor", "moderate", "serious", "critical"] = Field(
        description="axe-core impact level.",
    )
    description: str = Field(
        description="Human-readable description of the violation.",
    )
    help_url: str | None = Field(
        default=None,
        description="Link to axe documentation for the rule.",
    )
    affected_nodes: int = Field(
        ge=0,
        description="Number of DOM nodes flagged for this rule.",
    )


class A11yScanResult(BaseModel):
    """Aggregated accessibility scan result for a single page load.

    Enforces two cross-field invariants in its after-validator:
      * ``passed`` is true iff ``violations`` is empty.
      * ``total_affected_nodes`` equals the sum of affected_nodes
        across both ``violations`` and ``warnings``.
    """

    model_config = _RESPONSE_CONFIG

    url: str = Field(description="URL the scan was run against.")
    min_impact: Literal["minor", "moderate", "serious", "critical"] = Field(
        description="Minimum impact level treated as a failure.",
    )
    violations: tuple[A11yViolation, ...] = Field(
        description=("All violations at or above min_impact, sorted by impact."),
    )
    warnings: tuple[A11yViolation, ...] = Field(
        description=("Violations BELOW min_impact, surfaced for context only."),
    )
    total_affected_nodes: int = Field(
        ge=0,
        description="Sum of affected_nodes across all reported violations.",
    )
    scan_duration_seconds: float = Field(
        ge=0,
        description="Wall-clock time of the scan.",
    )
    axe_version: str = Field(description="Bundled axe-core version pin.")
    passed: bool = Field(
        description=("True when no violations at or above min_impact were found."),
    )

    @model_validator(mode="after")
    def _validate_passed_matches_violations(self) -> Self:
        """Enforce passed and aggregate invariants."""
        if self.passed != (len(self.violations) == 0):
            msg = (
                f"passed must equal (violations is empty); got "
                f"passed={self.passed}, violations_len={len(self.violations)}"
            )
            raise ValueError(msg)
        computed_total = sum(v.affected_nodes for v in self.violations) + sum(
            v.affected_nodes for v in self.warnings
        )
        if self.total_affected_nodes != computed_total:
            msg = (
                f"total_affected_nodes must equal sum of "
                f"violations + warnings affected_nodes; got "
                f"{self.total_affected_nodes}, computed {computed_total}"
            )
            raise ValueError(msg)
        return self


class NavigationResult(BaseModel):
    """Result of a navigation step."""

    model_config = _RESPONSE_CONFIG

    requested_url: str = Field(description="URL originally requested.")
    final_url: str = Field(
        description="Final URL after redirects (Playwright page.url).",
    )
    status_code: int | None = Field(
        default=None,
        ge=0,
        le=999,
        description=(
            "HTTP status code, if available. ``None`` for file:// URLs "
            "and other transports that do not surface a status code."
        ),
    )
    duration_seconds: float = Field(
        ge=0,
        description="Navigation wall-clock duration.",
    )


class ScreenshotMetadata(BaseModel):
    """Metadata for a captured screenshot."""

    model_config = _RESPONSE_CONFIG

    saved_path: str = Field(
        description="Path relative to the workspace root.",
    )
    width: int = Field(ge=1, description="Image width in pixels.")
    height: int = Field(ge=1, description="Image height in pixels.")
    file_size_bytes: int = Field(
        ge=0,
        description="On-disk size in bytes.",
    )
    full_page: bool = Field(
        description="True when the capture spans the full scrollable page.",
    )
    captured_at_iso: str = Field(
        description="UTC ISO 8601 capture timestamp.",
    )
    sha256: str = Field(
        pattern=SHA256_HEX_PATTERN,
        description="Lowercase hex SHA-256 (64 chars) of the captured PNG bytes.",
    )


class ScreenshotDiffResult(BaseModel):
    """Outcome of an SSIM comparison against a stored baseline.

    Enforces ``passed_tolerance ⟺ ssim_score >= tolerance`` so
    callers (LLM or controller) cannot be misled by a manually
    constructed result.
    """

    model_config = _RESPONSE_CONFIG

    spec_name: str = Field(description="Spec identifier compared.")
    screenshot_name: str = Field(description="Screenshot identifier compared.")
    ssim_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Computed SSIM (0.0 = unrelated, 1.0 = identical).",
    )
    tolerance: float = Field(
        ge=0.0,
        le=1.0,
        description="Pass threshold applied to ssim_score.",
    )
    passed_tolerance: bool = Field(
        description="True when ssim_score >= tolerance.",
    )
    baseline_path: str = Field(
        description="Workspace-relative path to the baseline PNG.",
    )
    current_path: str = Field(
        description="Workspace-relative path to the current capture.",
    )
    diff_image_path: str | None = Field(
        default=None,
        description=(
            "Workspace-relative path to the heatmap PNG. ``None`` when no "
            "comparison was performed (e.g. baseline freshly created)."
        ),
    )
    is_baseline_new: bool = Field(
        description=(
            "True when the baseline was created on this call "
            "(via create_baseline_if_missing)."
        ),
    )

    @model_validator(mode="after")
    def _validate_passed_matches_score(self) -> Self:
        """Enforce passed_tolerance equals ssim_score >= tolerance."""
        expected = self.ssim_score >= self.tolerance
        if self.passed_tolerance != expected:
            msg = (
                "passed_tolerance must equal (ssim_score >= tolerance); "
                f"got passed_tolerance={self.passed_tolerance}, "
                f"ssim_score={self.ssim_score}, tolerance={self.tolerance}"
            )
            raise ValueError(msg)
        return self


class SpecResult(BaseModel):
    """Aggregated result for a full spec run.

    A spec stitches together navigation, screenshot, diff, and
    accessibility scan in one invocation so the agent reasons over one
    structured payload per check-cycle.

    Enforces ``passed_all_checks ⟺ diff.passed_tolerance AND
    accessibility.passed``.
    """

    model_config = _RESPONSE_CONFIG

    spec_name: str = Field(description="Spec identifier.")
    viewport_width: int = Field(ge=1, description="Viewport width used.")
    viewport_height: int = Field(ge=1, description="Viewport height used.")
    navigation: NavigationResult = Field(description="Navigation outcome.")
    screenshot: ScreenshotMetadata = Field(
        description="Captured screenshot metadata.",
    )
    diff: ScreenshotDiffResult = Field(
        description="SSIM comparison against the baseline.",
    )
    accessibility: A11yScanResult = Field(
        description="Accessibility scan outcome.",
    )
    passed_all_checks: bool = Field(
        description=("True when diff.passed_tolerance and accessibility.passed."),
    )

    @model_validator(mode="after")
    def _validate_aggregate_pass(self) -> Self:
        """Enforce passed_all_checks reflects both nested results."""
        expected = self.diff.passed_tolerance and self.accessibility.passed
        if self.passed_all_checks != expected:
            msg = (
                "passed_all_checks must equal "
                "(diff.passed_tolerance and accessibility.passed); "
                f"got passed_all_checks={self.passed_all_checks}, "
                f"diff.passed_tolerance={self.diff.passed_tolerance}, "
                f"accessibility.passed={self.accessibility.passed}"
            )
            raise ValueError(msg)
        return self
