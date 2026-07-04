# module-kind: code
"""Host-side result builders parsing the in-container executor's JSON.

Split from :class:`BrowserTool` to keep that module under its module-size
budget. ``_BrowserBuilderMixin`` reads only ``self._baselines`` and
``self._settings``, both wired by ``BrowserTool.__init__``.
"""

import re
from pathlib import Path
from typing import Final, Literal, TypedDict

from pydantic import ValidationError

from synthorg.core.iso_datetime import now_iso_utc
from synthorg.observability import get_logger
from synthorg.observability.events.browser import (
    BROWSER_A11Y_SCAN_FAILED,
    BROWSER_NAVIGATE_FAILED,
    BROWSER_SCREENSHOT_FAILED,
    BROWSER_STORAGE_FAILED,
    BROWSER_WEBAUTHN_FAILED,
)
from synthorg.tools.browser._args import A11yImpact, BrowserToolArgs
from synthorg.tools.browser._baseline import WorkspaceBaselineStore
from synthorg.tools.browser._constants import AXE_VERSION_PIN, SHA256_HEX_LENGTH
from synthorg.tools.browser._models import (
    A11yScanResult,
    A11yViolation,
    NavigationResult,
    ScreenshotMetadata,
    StorageItemsResult,
    WebAuthnCredential,
    WebAuthnResult,
)
from synthorg.tools.browser._settings import BrowserSettings
from synthorg.tools.browser.errors import (
    BrowserAccessibilityError,
    BrowserNavigationError,
    BrowserScreenshotError,
    BrowserStorageError,
    BrowserWebAuthnError,
)

logger = get_logger(__name__)

_SHA256_HEX_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-f0-9]{64}$")


class _NavPayload(TypedDict, total=False):
    """Navigation sub-payload decoded from the in-container executor."""

    requested_url: str
    final_url: str
    status_code: int | None
    duration_seconds: float


class _ScreenshotPayload(TypedDict, total=False):
    """Screenshot sub-payload decoded from the in-container executor."""

    saved_path: str
    width: int
    height: int
    file_size_bytes: int
    full_page: bool
    sha256: str


class _A11yPayload(TypedDict, total=False):
    """Accessibility sub-payload decoded from the in-container executor."""

    url: str
    min_impact: A11yImpact
    violations: list[dict[str, object]]
    warnings: list[dict[str, object]]
    total_affected_nodes: int
    scan_duration_seconds: float
    axe_version: str
    passed: bool


class _StoragePayload(TypedDict, total=False):
    """WebStorage sub-payload decoded from the in-container executor."""

    storage_type: Literal["local", "session"]
    items: dict[str, str]


class _WebAuthnCredentialPayload(TypedDict, total=False):
    """A single virtual credential decoded from the in-container executor.

    Carries no private key: the executor keeps that host-side in the
    credential keystore and returns only the model-safe fields.
    """

    id: str
    rp_id: str
    user_handle: str
    public_key: str


class _WebAuthnPayload(TypedDict, total=False):
    """WebAuthn sub-payload decoded from the in-container executor."""

    credentials: list[_WebAuthnCredentialPayload]


class _ExecutorResult(TypedDict, total=False):
    """Top-level JSON envelope returned by the in-container executor."""

    status: str
    error_type: str
    message: str
    navigation: _NavPayload
    screenshot: _ScreenshotPayload
    accessibility: _A11yPayload
    storage: _StoragePayload
    webauthn: _WebAuthnPayload


class _BrowserBuilderMixin:
    """Parses the executor's JSON envelope into frozen response models."""

    _baselines: WorkspaceBaselineStore
    _settings: BrowserSettings

    def _build_navigation(
        self,
        payload: _ExecutorResult,
        requested_url: str,
    ) -> NavigationResult:
        """Build navigation.

        Returns:
            Result of type ``NavigationResult``.

        Raises:
            BrowserNavigationError: If the executor returned a malformed
                navigation payload.
        """
        nav_payload: _NavPayload = payload.get("navigation") or {}
        try:
            return NavigationResult(
                requested_url=requested_url,
                final_url=str(nav_payload.get("final_url", requested_url)),
                status_code=nav_payload.get("status_code"),
                duration_seconds=float(nav_payload.get("duration_seconds", 0.0)),
            )
        except (ValidationError, ValueError) as exc:
            logger.warning(BROWSER_NAVIGATE_FAILED, reason="invalid_navigation_payload")
            raise BrowserNavigationError(
                "Executor returned an invalid navigation payload"
            ) from exc

    def _build_screenshot(
        self,
        payload: _ExecutorResult,
        host_path: Path,
    ) -> ScreenshotMetadata:
        """Build screenshot.

        Returns:
            Result of type ``ScreenshotMetadata``.

        Raises:
            BrowserScreenshotError: If the related operation fails.
        """
        ss_payload: _ScreenshotPayload = payload.get("screenshot") or {}
        if not ss_payload:
            logger.warning(BROWSER_SCREENSHOT_FAILED, reason="no_screenshot_payload")
            raise BrowserScreenshotError(
                "Executor returned no screenshot payload",
            )
        sha = str(ss_payload.get("sha256", ""))
        if len(sha) != SHA256_HEX_LENGTH or _SHA256_HEX_PATTERN.match(sha) is None:
            logger.warning(
                BROWSER_SCREENSHOT_FAILED,
                reason="invalid_sha256",
                sha256_length=len(sha),
            )
            raise BrowserScreenshotError(
                "Executor returned an invalid sha256",
                context={"sha256_length": len(sha)},
            )
        try:
            return ScreenshotMetadata(
                saved_path=self._baselines.relative(host_path),
                width=int(
                    ss_payload.get("width", self._settings.viewport_width),
                ),
                height=int(
                    ss_payload.get("height", self._settings.viewport_height),
                ),
                file_size_bytes=int(ss_payload.get("file_size_bytes", 0)),
                full_page=bool(ss_payload.get("full_page", False)),
                captured_at_iso=now_iso_utc(),
                sha256=sha,
            )
        except (ValidationError, ValueError) as exc:
            logger.warning(
                BROWSER_SCREENSHOT_FAILED, reason="invalid_screenshot_payload"
            )
            raise BrowserScreenshotError(
                "Executor returned an invalid screenshot payload"
            ) from exc

    def _build_a11y(
        self,
        payload: _ExecutorResult,
        url: str,
        args: BrowserToolArgs,
    ) -> A11yScanResult:
        """Build a11y.

        Returns:
            Result of type ``A11yScanResult``.

        Raises:
            BrowserAccessibilityError: If the executor returned a malformed
                accessibility payload.
        """
        a11y_payload: _A11yPayload = payload.get("accessibility") or {}
        if not a11y_payload:
            return A11yScanResult(
                url=url,
                min_impact=args.min_impact,
                violations=(),
                warnings=(),
                total_affected_nodes=0,
                scan_duration_seconds=0.0,
                axe_version=AXE_VERSION_PIN,
                passed=True,
            )
        try:
            violations = tuple(
                A11yViolation.model_validate(v)
                for v in a11y_payload.get("violations", [])
            )
            warnings = tuple(
                A11yViolation.model_validate(v)
                for v in a11y_payload.get("warnings", [])
            )
            return A11yScanResult(
                url=str(a11y_payload.get("url", url)),
                min_impact=a11y_payload.get("min_impact", args.min_impact),
                violations=violations,
                warnings=warnings,
                total_affected_nodes=int(
                    a11y_payload.get("total_affected_nodes", 0),
                ),
                scan_duration_seconds=float(
                    a11y_payload.get("scan_duration_seconds", 0.0),
                ),
                axe_version=str(a11y_payload.get("axe_version", AXE_VERSION_PIN)),
                passed=bool(a11y_payload.get("passed", True)),
            )
        except (ValidationError, ValueError) as exc:
            logger.warning(BROWSER_A11Y_SCAN_FAILED, reason="invalid_a11y_payload")
            raise BrowserAccessibilityError(
                "Executor returned an invalid accessibility payload"
            ) from exc

    def _build_storage(
        self,
        payload: _ExecutorResult,
    ) -> StorageItemsResult:
        """Build storage.

        Returns:
            Result of type ``StorageItemsResult``.

        Raises:
            BrowserStorageError: If the related operation fails.
        """
        storage_payload: _StoragePayload = payload.get("storage") or {}
        if not storage_payload:
            logger.warning(BROWSER_STORAGE_FAILED, reason="no_storage_payload")
            raise BrowserStorageError("Executor returned no storage payload")
        try:
            return StorageItemsResult(
                storage_type=storage_payload.get("storage_type", "local"),
                items=storage_payload.get("items", {}),
            )
        except (ValidationError, ValueError) as exc:
            logger.warning(BROWSER_STORAGE_FAILED, reason="invalid_storage_payload")
            raise BrowserStorageError(
                "Executor returned an invalid storage payload"
            ) from exc

    def _build_webauthn(
        self,
        payload: _ExecutorResult,
    ) -> WebAuthnResult:
        """Build webauthn.

        Returns:
            Result of type ``WebAuthnResult``.

        Raises:
            BrowserWebAuthnError: If the related operation fails.
        """
        webauthn_payload: _WebAuthnPayload = payload.get("webauthn") or {}
        if not webauthn_payload:
            logger.warning(BROWSER_WEBAUTHN_FAILED, reason="no_webauthn_payload")
            raise BrowserWebAuthnError("Executor returned no webauthn payload")
        try:
            credentials = tuple(
                WebAuthnCredential.model_validate(c)
                for c in webauthn_payload.get("credentials", [])
            )
        except ValidationError as exc:
            logger.warning(BROWSER_WEBAUTHN_FAILED, reason="invalid_credential")
            raise BrowserWebAuthnError(
                "Executor returned an invalid webauthn credential"
            ) from exc
        return WebAuthnResult(credentials=credentials)
