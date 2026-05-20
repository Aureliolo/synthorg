"""Browser executor entry point shipped into the sandbox container.

The :class:`BrowserTool` copies this file into the project workspace
(``<workspace>/.synthorg/browser/executor.py``) on first use and then
runs it inside the configured DockerSandbox via
``sandbox.execute("python3", ("/workspace/.synthorg/browser/executor.py",))``.

This script is intentionally self-contained: it imports nothing from
``synthorg`` so it can run inside an arbitrary Playwright image. All
inputs arrive via the ``BROWSER_TOOL_ARGS_JSON`` environment variable
(a JSON-encoded payload); the result is written to stdout as JSON.

Payload schema (input)::

    {
        "operation": "capture" | "navigate" | "screenshot" | "accessibility_scan",
        "url": "file:///workspace/...",
        "viewport_width": 1280,
        "viewport_height": 720,
        "full_page": false,
        "wait_condition": "load",
        "navigation_timeout_seconds": 60.0,
        "launch_timeout_seconds": 30.0,
        "screenshot_path": "/workspace/.synthorg/screenshots/...",
        "axe_script_path": "/workspace/.synthorg/browser/axe.min.js" | null,
        "min_impact": "serious",
    }

Payload schema (output, on success)::

    {
        "status": "ok",
        "navigation": {...},
        "screenshot": {...} | null,
        "accessibility": {...} | null,
    }

On failure::

    {
        "status": "error",
        "error_type": "BrowserNavigationError",
        "message": "Executor failed",
    }
"""

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Final

_DEFAULT_VIEWPORT_WIDTH: Final[int] = 1280
_DEFAULT_VIEWPORT_HEIGHT: Final[int] = 720
_DEFAULT_NAV_TIMEOUT_SECONDS: Final[float] = 60.0
_DEFAULT_SCREENSHOT_TIMEOUT_SECONDS: Final[float] = 30.0
_DEFAULT_A11Y_TIMEOUT_SECONDS: Final[float] = 45.0
_DEFAULT_LAUNCH_TIMEOUT_SECONDS: Final[float] = 30.0
_MS_PER_SECOND: Final[int] = 1000
_AXE_SCRIPT_MAX_BYTES: Final[int] = 5 * 1024 * 1024

# Every filesystem path the executor touches must live under this root.
# The host BrowserTool always builds payload paths from
# ``CONTAINER_WORKSPACE_ROOT`` (``/workspace``); reject anything else
# before invoking ``Path(...).parent.mkdir`` / ``.stat()`` / ``.read_text``
# so a malformed payload cannot reach arbitrary container paths.
_SANDBOX_ROOT: Final[str] = "/workspace"


def _validated_sandbox_path(raw: str, *, field: str) -> Path:
    """Return ``Path(raw)`` after asserting it resolves under ``_SANDBOX_ROOT``.

    Used at every filesystem-touching site inside the executor so the
    user-controlled ``BROWSER_TOOL_ARGS_JSON`` payload cannot escape the
    sandbox workspace. ``..`` segments, absolute paths outside the root,
    and relative paths all raise ``ValueError`` so the boundary stays
    defensive against malformed callers.
    """
    if not raw:
        raise ValueError(f"{field} must be a non-empty path")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError(f"{field} must be an absolute path; got {raw!r}")
    if ".." in candidate.parts:
        raise ValueError(f"{field} must not contain '..' segments; got {raw!r}")
    # Resolve symlink-free against the sandbox root. ``resolve`` collapses
    # any remaining relative components and ``is_relative_to`` enforces
    # the containment invariant.
    resolved = candidate.resolve()
    sandbox = Path(_SANDBOX_ROOT).resolve()
    if not resolved.is_relative_to(sandbox):
        raise ValueError(
            f"{field} must resolve under {_SANDBOX_ROOT!r}; got {raw!r}",
        )
    return resolved


_A11Y_RANK = {
    "minor": 0,
    "moderate": 1,
    "serious": 2,
    "critical": 3,
}

# Browser-side wrapper: injects axe-core, runs the scan via Promise,
# and normalises each violation to the host-side schema.
_AXE_RUN_JS = """
async () => {
  return await new Promise((resolve) => {
    window.axe.run((err, results) => {
      if (err) {
        resolve({ error: String(err), violations: [] });
        return;
      }
      const out = results.violations.map((v) => ({
        rule_id: v.id,
        impact: v.impact,
        description: v.description,
        help_url: v.helpUrl,
        affected_nodes: v.nodes.length,
      }));
      resolve({ error: null, violations: out });
    });
  });
}
"""


async def _navigate(page: Any, payload: dict[str, Any]) -> dict[str, Any]:
    url = payload["url"]
    wait_condition = payload.get("wait_condition") or "load"
    timeout_seconds = float(
        payload.get("navigation_timeout_seconds") or _DEFAULT_NAV_TIMEOUT_SECONDS,
    )
    timeout_ms = int(timeout_seconds * _MS_PER_SECOND)
    start = time.monotonic()
    response = await page.goto(
        url,
        wait_until=wait_condition,
        timeout=timeout_ms,
    )
    duration = time.monotonic() - start
    status_code = response.status if response is not None else None
    return {
        "requested_url": url,
        "final_url": page.url,
        "status_code": status_code,
        "duration_seconds": duration,
    }


async def _screenshot(
    page: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    out = _validated_sandbox_path(
        payload["screenshot_path"],
        field="screenshot_path",
    )
    full_page = bool(payload.get("full_page", False))
    timeout_ms = int(_DEFAULT_SCREENSHOT_TIMEOUT_SECONDS * _MS_PER_SECOND)
    out.parent.mkdir(parents=True, exist_ok=True)
    png_bytes = await page.screenshot(
        path=str(out),
        full_page=full_page,
        timeout=timeout_ms,
    )
    width = payload.get("viewport_width") or _DEFAULT_VIEWPORT_WIDTH
    height = payload.get("viewport_height") or _DEFAULT_VIEWPORT_HEIGHT
    return {
        "saved_path": str(out),
        "width": int(width),
        "height": int(height),
        "file_size_bytes": out.stat().st_size,
        "full_page": full_page,
        "sha256": hashlib.sha256(png_bytes).hexdigest(),
    }


def _empty_a11y_result(url: str, min_impact: str) -> dict[str, Any]:
    """A11y result when no axe script is staged in the sandbox."""
    return {
        "url": url,
        "min_impact": min_impact,
        "violations": [],
        "warnings": [],
        "total_affected_nodes": 0,
        "scan_duration_seconds": 0.0,
        "axe_version": "unknown",
        "passed": True,
    }


def _partition_violations(
    raw_violations: list[dict[str, Any]],
    min_impact: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sort axe-core violations into (failing, warning) buckets."""
    min_rank = _A11Y_RANK[min_impact]
    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for entry in raw_violations:
        impact = entry.get("impact") or "minor"
        if impact not in _A11Y_RANK:
            continue
        bucket = violations if _A11Y_RANK[impact] >= min_rank else warnings
        bucket.append(
            {
                "rule_id": entry.get("rule_id", ""),
                "impact": impact,
                "description": entry.get("description", ""),
                "help_url": entry.get("help_url"),
                "affected_nodes": int(entry.get("affected_nodes", 0)),
            }
        )
    violations.sort(
        key=lambda v: (_A11Y_RANK[v["impact"]], v["rule_id"]),
        reverse=True,
    )
    warnings.sort(
        key=lambda v: (_A11Y_RANK[v["impact"]], v["rule_id"]),
        reverse=True,
    )
    return violations, warnings


def _load_axe_script(axe_script_path: str) -> str:
    """Read and size-check the bundled axe-core script."""
    axe_path = _validated_sandbox_path(axe_script_path, field="axe_script_path")
    if not axe_path.exists():
        raise FileNotFoundError(
            f"axe script not found inside sandbox: {axe_script_path}",
        )
    if axe_path.stat().st_size > _AXE_SCRIPT_MAX_BYTES:
        raise ValueError(f"axe script exceeds {_AXE_SCRIPT_MAX_BYTES} bytes")
    return axe_path.read_text(encoding="utf-8")


async def _accessibility(
    page: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    axe_script_path = payload.get("axe_script_path")
    min_impact = payload.get("min_impact") or "serious"
    if not axe_script_path:
        return _empty_a11y_result(page.url, min_impact)
    axe_source = _load_axe_script(axe_script_path)
    start = time.monotonic()
    await page.add_script_tag(content=axe_source)
    page.set_default_timeout(
        int(_DEFAULT_A11Y_TIMEOUT_SECONDS * _MS_PER_SECOND),
    )
    raw = await page.evaluate(_AXE_RUN_JS)
    duration = time.monotonic() - start
    if raw.get("error"):
        raise RuntimeError(f"axe-core internal error: {raw['error']}")
    violations, warnings = _partition_violations(raw["violations"], min_impact)
    total_affected = sum(v["affected_nodes"] for v in violations) + sum(
        v["affected_nodes"] for v in warnings
    )
    return {
        "url": page.url,
        "min_impact": min_impact,
        "violations": violations,
        "warnings": warnings,
        "total_affected_nodes": total_affected,
        "scan_duration_seconds": duration,
        "axe_version": payload.get("axe_version", "unknown"),
        "passed": not violations,
    }


async def _dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    # Playwright is only available inside the sandbox image where this
    # script executes; importing lazily keeps the executor importable
    # for host-side static analysis without a host playwright install.
    from playwright.async_api import async_playwright  # noqa: PLC0415

    operation = payload["operation"]
    width = int(
        payload.get("viewport_width") or _DEFAULT_VIEWPORT_WIDTH,
    )
    height = int(
        payload.get("viewport_height") or _DEFAULT_VIEWPORT_HEIGHT,
    )
    launch_timeout_seconds = float(
        payload.get("launch_timeout_seconds") or _DEFAULT_LAUNCH_TIMEOUT_SECONDS,
    )

    async with async_playwright() as pw:
        # Bound the Chromium boot step so a slow launch consumes the
        # launch budget rather than silently eating into the navigation
        # budget. ``TimeoutError`` is mapped to BrowserLaunchError on the
        # host side via the executor-error map.
        browser = await asyncio.wait_for(
            pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                    "--no-sandbox",
                ],
            ),
            timeout=launch_timeout_seconds,
        )
        try:
            context = await browser.new_context(
                viewport={"width": width, "height": height},
            )
            page = await context.new_page()
            try:
                navigation = await _navigate(page, payload)
                screenshot_result: dict[str, Any] | None = None
                a11y_result: dict[str, Any] | None = None
                if operation in {"capture", "screenshot"}:
                    if not payload.get("screenshot_path"):
                        raise ValueError(
                            "screenshot_path required for capture / screenshot",
                        )
                    screenshot_result = await _screenshot(page, payload)
                if operation in {"capture", "accessibility_scan"}:
                    a11y_result = await _accessibility(page, payload)
                return {
                    "status": "ok",
                    "navigation": navigation,
                    "screenshot": screenshot_result,
                    "accessibility": a11y_result,
                }
            finally:
                await page.close()
                await context.close()
        finally:
            await browser.close()


def main() -> int:
    raw = os.environ.get("BROWSER_TOOL_ARGS_JSON")
    if not raw:
        sys.stdout.write(
            json.dumps(
                {
                    "status": "error",
                    "error_type": "BrowserArgumentError",
                    "message": "BROWSER_TOOL_ARGS_JSON env var is required",
                },
            ),
        )
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "status": "error",
                    "error_type": "BrowserArgumentError",
                    "message": f"invalid JSON args: {exc.msg}",
                },
            ),
        )
        return 2
    try:
        result = asyncio.run(_dispatch(payload))
    except Exception as exc:
        # Redact the raw exception message entirely: str(exc) can carry
        # filesystem paths, env vars, URLs, or page content. Emit only
        # the exception class name plus a static generic message so the
        # host side has a stable shape without leaking secrets.
        sys.stdout.write(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": "Executor failed",
                },
            ),
        )
        return 1
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
