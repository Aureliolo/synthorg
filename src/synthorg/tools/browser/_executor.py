# module-kind: integration
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
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from playwright.async_api import Browser, BrowserContext, Page, VirtualCredential

if TYPE_CHECKING:
    # The executor is copied into and run inside the sandbox container, where
    # ``synthorg`` is not installed, so this import must never execute at
    # runtime. Signature annotations referencing these names are quoted so the
    # sub-3.14 sandbox interpreter never evaluates them at function definition.
    from synthorg.tools.browser._executor_types import (
        BrowserPayload,
        StoragePayload,
        Violation,
        WebAuthnCredentialPayload,
        WebAuthnKeystoreEntry,
        WebAuthnPayload,
    )

_STORAGE_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"storage_get", "storage_set", "storage_remove", "storage_clear"},
)
_WEBAUTHN_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "webauthn_install",
        "webauthn_create_credential",
        "webauthn_list_credentials",
        "webauthn_delete_credential",
    },
)

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


def _reraise_critical(exc: BaseException) -> None:
    """Re-raise ``exc`` when it is an interpreter-critical exception.

    Dependency-free re-implementation of
    ``synthorg.core.critical_errors.reraise_critical`` so this executor
    stays self-contained and importable inside an arbitrary Playwright
    image that has no ``synthorg`` package installed.

    Returns silently when ``exc`` is neither ``MemoryError`` nor
    ``RecursionError``, so the caller continues its normal flow.

    Args:
        exc: The caught exception, inspected before any error handling.

    Raises:
        MemoryError: Re-raised unchanged when ``exc`` is a ``MemoryError``.
        RecursionError: Re-raised unchanged when ``exc`` is a
            ``RecursionError``.
    """
    if isinstance(exc, (MemoryError, RecursionError)):
        raise exc


def _validated_sandbox_path(raw: str, *, field: str) -> Path:
    """Return ``Path(raw)`` after asserting it resolves under ``_SANDBOX_ROOT``.

    Used at every filesystem-touching site inside the executor so the
    user-controlled ``BROWSER_TOOL_ARGS_JSON`` payload cannot escape the
    sandbox workspace. ``..`` segments, absolute paths outside the root,
    and relative paths all raise ``ValueError`` so the boundary stays
    defensive against malformed callers.

    Returns:
        Result of type ``Path``.

    Raises:
        ValueError: If an argument fails domain validation.
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


async def _navigate(page: Page, payload: "BrowserPayload") -> dict[str, object]:
    """Navigate.

    Returns:
        Mapping from ``str`` to ``object``.
    """
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
    page: Page,
    payload: "BrowserPayload",
) -> dict[str, object]:
    """Screenshot.

    Returns:
        Mapping from ``str`` to ``object``.
    """
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


def _empty_a11y_result(url: str, min_impact: str) -> dict[str, object]:
    """A11y result when no axe script is staged in the sandbox.

    Returns:
        Mapping from ``str`` to ``object``.
    """
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
    raw_violations: list["Violation"],
    min_impact: str,
) -> tuple[list["Violation"], list["Violation"]]:
    """Sort axe-core violations into (failing, warning) buckets.

    Returns:
        Tuple of (failing, warning) violation lists.
    """
    min_rank = _A11Y_RANK[min_impact]
    violations: list["Violation"] = []
    warnings: list["Violation"] = []
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
    """Read and size-check the bundled axe-core script.

    Returns:
        Result of type ``str``.

    Raises:
        FileNotFoundError: If the target path does not exist.
        ValueError: If an argument fails domain validation.
    """
    axe_path = _validated_sandbox_path(axe_script_path, field="axe_script_path")
    if not axe_path.exists():
        raise FileNotFoundError(
            f"axe script not found inside sandbox: {axe_script_path}",
        )
    if axe_path.stat().st_size > _AXE_SCRIPT_MAX_BYTES:
        raise ValueError(f"axe script exceeds {_AXE_SCRIPT_MAX_BYTES} bytes")
    return axe_path.read_text(encoding="utf-8")


async def _accessibility(
    page: Page,
    payload: "BrowserPayload",
) -> dict[str, object]:
    """Accessibility.

    Returns:
        Mapping from ``str`` to ``object``.

    Raises:
        RuntimeError: If the operation fails at runtime.
    """
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


async def _storage_get(page: Page, payload: "BrowserPayload") -> "StoragePayload":
    """Read a single named item from the page's WebStorage.

    A key is required: dumping the whole store would surface every value
    a page holds (session tokens, embedded API keys) unfiltered into the
    model-facing result, so reads must name the exact key.

    Returns:
        The storage type plus the matching key/value pair (empty when the
        key is absent).

    Raises:
        ValueError: If ``storage_key`` is missing from the payload.
    """
    storage_type = payload.get("storage_type") or "local"
    storage = page.session_storage if storage_type == "session" else page.local_storage
    key = payload.get("storage_key")
    if not key:
        raise ValueError("storage_get requires storage_key")
    value = await storage.get_item(key)
    items = {key: value} if value is not None else {}
    return {"storage_type": storage_type, "items": items}


async def _storage_set(page: Page, payload: "BrowserPayload") -> "StoragePayload":
    """Write one item to the page's WebStorage.

    Returns:
        The storage type plus the written key/value pair.
    """
    storage_type = payload.get("storage_type") or "local"
    storage = page.session_storage if storage_type == "session" else page.local_storage
    key = payload["storage_key"]
    value = payload["storage_value"]
    await storage.set_item(key, value)
    return {"storage_type": storage_type, "items": {key: value}}


async def _storage_remove(page: Page, payload: "BrowserPayload") -> "StoragePayload":
    """Remove one item from the page's WebStorage.

    Returns:
        The storage type with an empty items mapping.
    """
    storage_type = payload.get("storage_type") or "local"
    storage = page.session_storage if storage_type == "session" else page.local_storage
    await storage.remove_item(payload["storage_key"])
    return {"storage_type": storage_type, "items": {}}


async def _storage_clear(page: Page, payload: "BrowserPayload") -> "StoragePayload":
    """Clear all items from the page's WebStorage.

    Returns:
        The storage type with an empty items mapping.
    """
    storage_type = payload.get("storage_type") or "local"
    storage = page.session_storage if storage_type == "session" else page.local_storage
    await storage.clear()
    return {"storage_type": storage_type, "items": {}}


_STORAGE_HANDLERS: Final[
    dict[str, Callable[[Page, "BrowserPayload"], Awaitable["StoragePayload"]]]
] = {
    "storage_get": _storage_get,
    "storage_set": _storage_set,
    "storage_remove": _storage_remove,
    "storage_clear": _storage_clear,
}


def _credential_to_payload(
    cred: VirtualCredential,
) -> "WebAuthnCredentialPayload":
    """Normalize a ``VirtualCredential`` to the model-safe host shape.

    Playwright returns camelCase keys (``rpId``, ``userHandle``, ...); the
    host-side model expects snake_case. The private key is deliberately
    omitted: it is a secret and must never reach the model-facing result.

    Returns:
        The credential's non-secret fields in snake_case form.
    """
    return {
        "id": cred["id"],
        "rp_id": cred["rpId"],
        "user_handle": cred["userHandle"],
        "public_key": cred["publicKey"],
    }


def _credential_to_keystore_entry(
    cred: VirtualCredential,
) -> "WebAuthnKeystoreEntry":
    """Normalize a ``VirtualCredential`` to the full host-side keystore shape.

    Includes the private key so the authenticator can be re-seeded on a
    later call. Written only to the workspace-mounted keystore file, never
    to the result returned to the model.

    Returns:
        The full credential tuple in snake_case form.
    """
    return {
        "id": cred["id"],
        "rp_id": cred["rpId"],
        "user_handle": cred["userHandle"],
        "private_key": cred["privateKey"],
        "public_key": cred["publicKey"],
    }


def _load_keystore(path: str | None) -> list["WebAuthnKeystoreEntry"]:
    """Load the virtual-authenticator credential keystore from the workspace.

    Returns:
        The stored credential tuples, or an empty list when no keystore
        exists yet or it cannot be parsed.
    """
    if not path:
        return []
    validated = _validated_sandbox_path(path, field="webauthn_state_path")
    if not validated.exists():
        return []
    try:
        data = json.loads(validated.read_text(encoding="utf-8"))
    except json.JSONDecodeError, OSError:
        return []
    return data if isinstance(data, list) else []


def _save_keystore(path: str, entries: list["WebAuthnKeystoreEntry"]) -> None:
    """Persist the credential keystore to the workspace-mounted path."""
    validated = _validated_sandbox_path(path, field="webauthn_state_path")
    validated.parent.mkdir(parents=True, exist_ok=True)
    validated.write_text(json.dumps(entries), encoding="utf-8")


async def _seed_authenticator(
    context: BrowserContext,
    entries: list["WebAuthnKeystoreEntry"],
) -> None:
    """Install the virtual authenticator and re-seed every stored credential.

    Re-seeding imports each keystore credential (id + keys + user handle)
    so ``webauthn_list``/``delete`` see prior credentials and a page's
    ``navigator.credentials.get()`` ceremony is answered during browsing.
    """
    await context.credentials.install()
    for entry in entries:
        await context.credentials.create(
            rp_id=entry["rp_id"],
            id=entry["id"],
            user_handle=entry["user_handle"],
            private_key=entry["private_key"],
            public_key=entry["public_key"],
        )


async def _webauthn(
    context: BrowserContext,
    payload: "BrowserPayload",
    entries: list["WebAuthnKeystoreEntry"],
) -> "WebAuthnPayload":
    """Run one WebAuthn virtual-authenticator operation.

    The authenticator is already installed and re-seeded from the keystore
    by ``_run_in_context`` before this runs, so ``list``/``delete`` act on
    the persisted credentials. ``create`` appends the new full credential
    (private key included) to the workspace keystore; ``delete`` removes it.
    Only the non-secret credential fields are ever returned to the host.

    Returns:
        The credentials produced or returned by this operation.

    Raises:
        KeyError: If a required payload field is missing for this operation.
    """
    operation = payload["operation"]
    state_path = payload.get("webauthn_state_path")
    if operation == "webauthn_install":
        return {"credentials": []}
    if operation == "webauthn_create_credential":
        cred = await context.credentials.create(
            rp_id=payload["webauthn_rp_id"],
            user_handle=payload.get("webauthn_user_handle"),
        )
        if state_path:
            _save_keystore(state_path, [*entries, _credential_to_keystore_entry(cred)])
        return {"credentials": [_credential_to_payload(cred)]}
    if operation == "webauthn_list_credentials":
        creds = await context.credentials.get(rp_id=payload.get("webauthn_rp_id"))
        return {"credentials": [_credential_to_payload(c) for c in creds]}
    # webauthn_delete_credential
    target_id = payload["webauthn_credential_id"]
    await context.credentials.delete(id=target_id)
    if state_path:
        _save_keystore(state_path, [e for e in entries if e["id"] != target_id])
    return {"credentials": []}


async def _new_context(
    browser: Browser,
    payload: "BrowserPayload",
    width: int,
    height: int,
) -> BrowserContext:
    """Create a context, seeding it from the persisted storage_state if present.

    Returns:
        A browser context pre-loaded with any saved cookies/localStorage.
    """
    state_path = payload.get("storage_state_path")
    if state_path:
        validated = _validated_sandbox_path(state_path, field="storage_state_path")
        if validated.exists():
            return await browser.new_context(
                viewport={"width": width, "height": height},
                storage_state=str(validated),
            )
    return await browser.new_context(
        viewport={"width": width, "height": height},
    )


async def _persist_storage_state(
    context: BrowserContext,
    payload: "BrowserPayload",
) -> None:
    """Save the context's cookies + localStorage back to the workspace.

    Called only on the page path (a navigation has materialised the
    origin's localStorage) so a webauthn-only call cannot clobber a
    previously-saved storage snapshot with an empty one.
    """
    state_path = payload.get("storage_state_path")
    if not state_path:
        return
    validated = _validated_sandbox_path(state_path, field="storage_state_path")
    validated.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(validated))


async def _sync_keystore(
    context: BrowserContext,
    payload: "BrowserPayload",
    entries: list["WebAuthnKeystoreEntry"],
) -> None:
    """Merge any page-registered passkeys back into the workspace keystore.

    After a navigation with the authenticator seeded, a page may have
    registered its own passkey via ``navigator.credentials.create()``;
    capture those so they survive to the next call.
    """
    state_path = payload.get("webauthn_state_path")
    if not state_path:
        return
    merged: "dict[str, WebAuthnKeystoreEntry]" = {e["id"]: e for e in entries}
    for cred in await context.credentials.get():
        merged[cred["id"]] = _credential_to_keystore_entry(cred)
    _save_keystore(state_path, list(merged.values()))


async def _dispatch_page(
    context: BrowserContext,
    payload: "BrowserPayload",
    *,
    seeded: bool,
    entries: list["WebAuthnKeystoreEntry"],
) -> dict[str, object]:
    """Run the page-scoped operations (navigate, screenshot, a11y, storage).

    Returns:
        The result envelope for a page-path operation.

    Raises:
        ValueError: If a required payload field is missing.
    """
    operation = payload["operation"]
    page = await context.new_page()
    try:
        navigation = await _navigate(page, payload)
        screenshot_result: dict[str, object] | None = None
        a11y_result: dict[str, object] | None = None
        storage_result: "StoragePayload | None" = None
        if operation in {"capture", "screenshot"}:
            if not payload.get("screenshot_path"):
                raise ValueError("screenshot_path required for capture / screenshot")
            screenshot_result = await _screenshot(page, payload)
        if operation in {"capture", "accessibility_scan"}:
            a11y_result = await _accessibility(page, payload)
        if operation in _STORAGE_OPERATIONS:
            storage_result = await _STORAGE_HANDLERS[operation](page, payload)
        # Persist session state while the page (and its origin's
        # localStorage) is still open, so a later call sees the writes.
        await _persist_storage_state(context, payload)
        if seeded:
            await _sync_keystore(context, payload, entries)
        return {
            "status": "ok",
            "navigation": navigation,
            "screenshot": screenshot_result,
            "accessibility": a11y_result,
            "storage": storage_result,
        }
    finally:
        await page.close()


async def _run_in_context(
    browser: Browser,
    payload: "BrowserPayload",
    width: int,
    height: int,
) -> dict[str, object]:
    """Seed session state, then dispatch the operation within one context.

    Returns:
        The operation's result envelope.
    """
    operation = payload["operation"]
    entries = _load_keystore(payload.get("webauthn_state_path"))
    context = await _new_context(browser, payload, width, height)
    try:
        # Seed the virtual authenticator for any webauthn op, and for a
        # page path only once the agent has created credentials -- so a
        # navigation answers a passkey ceremony while a plain browse with
        # no stored passkeys keeps the native (absent) behaviour.
        seeded = operation in _WEBAUTHN_OPERATIONS or bool(entries)
        if seeded:
            await _seed_authenticator(context, entries)
        if operation in _WEBAUTHN_OPERATIONS:
            webauthn_result = await _webauthn(context, payload, entries)
            return {"status": "ok", "webauthn": webauthn_result}
        return await _dispatch_page(context, payload, seeded=seeded, entries=entries)
    finally:
        await context.close()


async def _dispatch(payload: "BrowserPayload") -> dict[str, object]:
    # Playwright is only available inside the sandbox image where this
    # script executes; importing lazily keeps the executor importable
    # for host-side static analysis without a host playwright install.
    """Dispatch.

    Returns:
        Mapping from ``str`` to ``object``.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    from playwright.async_api import async_playwright  # noqa: PLC0415

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
            return await _run_in_context(browser, payload, width, height)
        finally:
            await browser.close()


def main() -> int:
    """Main.

    Returns:
        Result of type ``int``.
    """
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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        _reraise_critical(exc)
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
