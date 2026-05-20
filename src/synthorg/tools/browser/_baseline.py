"""Workspace-scoped baseline screenshot store.

Reads and writes baseline PNG plus sidecar metadata under
``<workspace>/.synthorg/screenshots/<spec>/``. The persistence
boundary is filesystem-only; no sqlite / psycopg involvement.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 -- runtime use in workspace resolution
from typing import Final

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.browser import (
    BROWSER_BASELINE_CREATED,
    BROWSER_BASELINE_SIDECAR_WRITTEN,
    BROWSER_BASELINE_WRITE_FAILED,
)
from synthorg.tools.browser._constants import (
    AXE_VERSION_PIN,
    BASELINE_META_FILENAME,
    BROWSER_IMAGE_PIN_DEFAULT,
    SCREENSHOTS_SUBDIR,
)
from synthorg.tools.browser.errors import (
    BrowserBaselineNotFoundError,
    BrowserDomainError,
)

logger = get_logger(__name__)

_FORBIDDEN_SEGMENTS: Final[frozenset[str]] = frozenset({"..", "."})


class WorkspaceBaselineStore:
    """Resolves baseline paths and writes / reads sidecar metadata."""

    def __init__(self, *, workspace: Path) -> None:
        """Initialise the store rooted at the persistent workspace."""
        if not workspace.is_absolute():
            msg = f"workspace must be absolute, got {workspace!r}"
            raise BrowserDomainError(msg)
        self._workspace = workspace.resolve()
        self._root = self._workspace / SCREENSHOTS_SUBDIR

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def root(self) -> Path:
        return self._root

    def spec_dir(self, *, spec_name: str) -> Path:
        """Return the directory holding baselines for one spec.

        Raises:
            BrowserDomainError: If ``spec_name`` contains path-traversal
                segments or absolute components.
        """
        self._reject_traversal(spec_name)
        return self._root / spec_name

    def baseline_path(
        self,
        *,
        spec_name: str,
        screenshot_name: str,
    ) -> Path:
        """Return the canonical baseline PNG path."""
        self._reject_traversal(screenshot_name)
        return self.spec_dir(spec_name=spec_name) / f"{screenshot_name}.png"

    def current_path(
        self,
        *,
        spec_name: str,
        screenshot_name: str,
    ) -> Path:
        """Return the path used for a fresh capture (sibling to baseline)."""
        self._reject_traversal(screenshot_name)
        return self.spec_dir(spec_name=spec_name) / f"{screenshot_name}.current.png"

    def diff_path(
        self,
        *,
        spec_name: str,
        screenshot_name: str,
    ) -> Path:
        """Return the path for the diff heatmap."""
        self._reject_traversal(screenshot_name)
        return self.spec_dir(spec_name=spec_name) / f"{screenshot_name}.diff.png"

    def relative(self, absolute_path: Path) -> str:
        """Return a path expressed relative to the workspace root."""
        return absolute_path.resolve().relative_to(self._workspace).as_posix()

    def write_sidecar(
        self,
        *,
        spec_name: str,
        screenshot_name: str,
        png_bytes: bytes,
        chromium_image: str = BROWSER_IMAGE_PIN_DEFAULT,
    ) -> Path:
        """Persist ``.meta.json`` alongside a baseline.

        The sidecar records image pin, capture time, sha256, and the
        axe-core version pin so future regression debugging has a
        provenance trail.
        """
        self._reject_traversal(screenshot_name)
        meta_path = (
            self.spec_dir(spec_name=spec_name)
            / f"{screenshot_name}{BASELINE_META_FILENAME}"
        )
        payload = {
            "spec_name": spec_name,
            "screenshot_name": screenshot_name,
            "captured_at_iso": datetime.now(UTC).isoformat(),
            "chromium_image": chromium_image,
            "axe_version": AXE_VERSION_PIN,
            "sha256": hashlib.sha256(png_bytes).hexdigest(),
        }
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                BROWSER_BASELINE_WRITE_FAILED,
                spec=spec_name,
                screenshot=screenshot_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise BrowserDomainError(
                "Failed to write baseline sidecar",
                context={"error_type": type(exc).__name__},
            ) from exc
        logger.info(
            BROWSER_BASELINE_SIDECAR_WRITTEN,
            spec=spec_name,
            screenshot=screenshot_name,
            meta_path=str(meta_path),
        )
        return meta_path

    def adopt_current_as_baseline(
        self,
        *,
        spec_name: str,
        screenshot_name: str,
    ) -> Path:
        """Promote the just-captured file to the baseline slot.

        Returns the new baseline path. Logs
        ``BROWSER_BASELINE_CREATED`` so operators can audit when
        baselines first land.
        """
        baseline = self.baseline_path(
            spec_name=spec_name,
            screenshot_name=screenshot_name,
        )
        current = self.current_path(
            spec_name=spec_name,
            screenshot_name=screenshot_name,
        )
        if not current.exists():
            raise BrowserBaselineNotFoundError(
                "Cannot promote: no current capture exists",
                context={"current": str(current)},
            )
        baseline.parent.mkdir(parents=True, exist_ok=True)
        current.replace(baseline)
        logger.info(
            BROWSER_BASELINE_CREATED,
            spec=spec_name,
            screenshot=screenshot_name,
            baseline=str(baseline),
        )
        return baseline

    @staticmethod
    def _reject_traversal(name: str) -> None:
        """Reject names containing ``..`` segments or path separators."""
        if not name:
            raise BrowserDomainError("baseline name must be non-empty")
        if "/" in name or "\\" in name:
            raise BrowserDomainError(
                "baseline name must not contain path separators",
                context={"name": name},
            )
        if name in _FORBIDDEN_SEGMENTS:
            raise BrowserDomainError(
                "baseline name must not be '.' or '..'",
                context={"name": name},
            )
