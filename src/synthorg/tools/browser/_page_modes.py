# module-kind: code
"""Page-opening mode handlers for :class:`BrowserTool`.

Split from that module to keep it under its module-size budget, mirroring
``_builders.py``. ``_PageModesMixin`` calls only ``_resolve_url``,
``_run_executor`` and ``_build_navigation``, all provided by ``BrowserTool``
and its builder mixin.

Both modes open a page and report on it, differing only in what they return,
which is why they share one path here.
"""

from typing import Protocol

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.browser import (
    BROWSER_NAVIGATE_FAILED,
    BROWSER_NAVIGATE_START,
    BROWSER_NAVIGATE_SUCCESS,
)
from synthorg.providers.url_utils import redact_url
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.browser._args import BrowserToolArgs
from synthorg.tools.browser._builders import _ExecutorResult
from synthorg.tools.browser._models import NavigationResult, PageContentResult
from synthorg.tools.browser._result_helpers import ok_result
from synthorg.tools.browser.errors import BrowserDomainError

logger = get_logger(__name__)


class _PageModeHost(Protocol):
    """What the mixin needs from the tool it is mixed into."""

    def _resolve_url(self, args: BrowserToolArgs) -> str: ...

    async def _run_executor(
        self,
        *,
        operation: str,
        url: str,
        args: BrowserToolArgs,
        screenshot_path: str | None = None,
    ) -> _ExecutorResult: ...

    def _build_navigation(
        self,
        payload: _ExecutorResult,
        requested_url: str,
    ) -> NavigationResult: ...

    async def _open_page(
        self,
        args: BrowserToolArgs,
        *,
        operation: str,
    ) -> tuple[_ExecutorResult, NavigationResult]: ...


class _PageModesMixin:
    """The ``navigate`` and ``content`` mode handlers."""

    async def _open_page(
        self: _PageModeHost,
        args: BrowserToolArgs,
        *,
        operation: str,
    ) -> tuple[_ExecutorResult, NavigationResult]:
        """Run a page-opening operation and parse its navigation result.

        Returns:
            The raw executor payload and the parsed navigation result.

        Raises:
            BrowserDomainError: If the related operation fails.
        """
        url = self._resolve_url(args)
        logger.debug(BROWSER_NAVIGATE_START, url=redact_url(url))
        try:
            payload = await self._run_executor(
                operation=operation,
                url=url,
                args=args,
            )
            navigation = self._build_navigation(payload, url)
        except BrowserDomainError as exc:
            logger.warning(
                BROWSER_NAVIGATE_FAILED,
                url=redact_url(url),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.debug(BROWSER_NAVIGATE_SUCCESS, url=redact_url(navigation.final_url))
        return payload, navigation

    async def _mode_navigate(
        self: _PageModeHost,
        args: BrowserToolArgs,
    ) -> ToolExecutionResult:
        """Mode navigate.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            BrowserDomainError: If the related operation fails.
        """
        _, navigation = await self._open_page(args, operation="navigate")
        return ok_result(navigation)

    async def _mode_content(
        self: _PageModeHost,
        args: BrowserToolArgs,
    ) -> ToolExecutionResult:
        """Mode content: navigate, then return the DOM after scripts have run.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            BrowserDomainError: If the related operation fails.
        """
        payload, navigation = await self._open_page(args, operation="content")
        raw = payload.get("content")
        html = raw if isinstance(raw, str) else ""
        return ok_result(
            PageContentResult(
                requested_url=navigation.requested_url,
                final_url=navigation.final_url,
                html=html,
                content_length=len(html),
            )
        )


__all__ = ["_PageModesMixin"]
