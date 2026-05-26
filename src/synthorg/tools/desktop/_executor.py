"""Desktop executor entry point shipped into the sandbox container.

The :class:`DesktopTool` copies this file into the project workspace
(``<workspace>/.synthorg/desktop/executor.py``) on first use and then
runs it inside the configured DockerSandbox via
``sandbox.execute("python3", ("/workspace/.synthorg/desktop/executor.py",))``.

This script is intentionally self-contained: it imports nothing from
``synthorg`` so it can run inside an arbitrary Xvfb-capable image. All
inputs arrive via the ``DESKTOP_TOOL_ARGS_JSON`` environment variable
(a JSON-encoded payload); the result is written to stdout as JSON.

It drives a headless X session: Xvfb (plus optional x11vnc) is started
idempotently and survives the Python process exit (``setsid``), so a
warm per-agent sandbox container keeps the display and the launched GUI
app alive across successive tool calls. Input is injected with
``xdotool``; screenshots are captured with ``scrot``.

Payload schema (input)::

    {
        "operation": "launch" | "click" | "type" | "key" | "screenshot" | "scroll",
        "session": {"display": ":99", "screen_width": 1280, ...},
        "app_command": "python3 /workspace/app.py",
        "x": 100, "y": 200, "button": 1, "double": false,
        "text": "hello", "keys": "ctrl+s",
        "direction": "down", "amount": 3,
        "screenshot_path": "/workspace/.synthorg/desktop/screenshots/x.png",
        "settle_delay_seconds": 0.2,
        "launch_timeout_seconds": 30.0
    }

Payload schema (output, on success)::

    {"status": "ok", "result": {...}}

On failure::

    {"status": "error", "error_type": "DesktopLaunchError", "message": "..."}
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Final

from synthorg.core.critical_errors import reraise_critical

_SANDBOX_ROOT: Final[str] = "/workspace"
_SESSION_STATE_PATH: Final[str] = "/workspace/.synthorg/desktop/session.json"

_DEFAULT_DISPLAY: Final[str] = ":99"
_DEFAULT_SCREEN_WIDTH: Final[int] = 1280
_DEFAULT_SCREEN_HEIGHT: Final[int] = 800
_DEFAULT_COLOR_DEPTH: Final[int] = 24
_DEFAULT_VNC_PORT: Final[int] = 5900

_SESSION_POLL_SECONDS: Final[float] = 0.25
_SESSION_START_TIMEOUT_SECONDS: Final[float] = 30.0
_LAUNCH_POLL_SECONDS: Final[float] = 0.25
_PNG_IHDR_WIDTH_OFFSET: Final[int] = 16
_PNG_IHDR_HEIGHT_OFFSET: Final[int] = 20
_UINT32_BYTES: Final[int] = 4
_SUBPROCESS_TIMEOUT_SECONDS: Final[float] = 15.0
_SCROLL_BUTTON_UP: Final[int] = 4
_SCROLL_BUTTON_DOWN: Final[int] = 5


def _validated_sandbox_path(raw: str, *, field: str) -> Path:
    """Return ``Path(raw)`` after asserting it resolves under the sandbox root.

    Used at every filesystem-touching site so the user-controlled
    ``DESKTOP_TOOL_ARGS_JSON`` payload cannot escape the workspace.

    Returns:
        Result of type ``Path``.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if not raw:
        raise ValueError(f"{field} must be a non-empty path")
    # The container is always Linux, so validate with POSIX semantics
    # regardless of the host OS that ships this script. Rejecting ``..``
    # up front means containment can be checked purely (no filesystem).
    candidate = PurePosixPath(raw)
    if not candidate.is_absolute():
        raise ValueError(f"{field} must be an absolute path; got {raw!r}")
    if ".." in candidate.parts:
        raise ValueError(f"{field} must not contain '..' segments; got {raw!r}")
    if not candidate.is_relative_to(PurePosixPath(_SANDBOX_ROOT)):
        raise ValueError(f"{field} must resolve under {_SANDBOX_ROOT!r}; got {raw!r}")
    # Lexical containment is not enough: a path under the sandbox root
    # can still traverse out through a symlinked ancestor. Resolve both
    # sides on the real filesystem (strict=False keeps non-existent
    # leaf targets lexical) and re-check containment against the
    # resolved root so symlink escapes are rejected.
    sandbox_root = Path(_SANDBOX_ROOT).resolve()
    resolved = Path(raw).resolve(strict=False)
    if not resolved.is_relative_to(sandbox_root):
        raise ValueError(f"{field} must resolve under {_SANDBOX_ROOT!r}; got {raw!r}")
    return resolved


_DISPLAY_PATTERN: Final = re.compile(r"^:\d+(\.\d+)?$")


def _validated_display(raw: object) -> str:
    """Return an X display name after asserting it is well-formed.

    ``display`` originates in the user-controlled
    ``DESKTOP_TOOL_ARGS_JSON`` payload and is interpolated into the
    Xvfb / x11vnc command lines. Constraining it to the canonical
    ``:N`` / ``:N.S`` form (allowlist, not denylist) blocks any
    argument-injection vector before the value reaches a subprocess.

    Returns:
        Result of type ``str``.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    display = str(raw or _DEFAULT_DISPLAY)
    if not _DISPLAY_PATTERN.fullmatch(display):
        raise ValueError(f"display must match ':N' or ':N.S'; got {display!r}")
    return display


def _display_env(display: str) -> dict[str, str]:
    """Return an environment with DISPLAY pinned to *display*.

    Returns:
        Mapping from ``str`` to ``str``.
    """
    env = dict(os.environ)
    env["DISPLAY"] = display
    return env


def _display_up(display: str) -> bool:
    """Return True when an X server answers on *display*.

    Returns:
        ``True`` if the operation succeeds, ``False`` otherwise.
    """
    try:
        completed = subprocess.run(  # noqa: S603
            ["xdpyinfo", "-display", display],  # noqa: S607
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return False
    return completed.returncode == 0


def _start_session(session: dict[str, Any]) -> None:
    """Bring up Xvfb (and optional x11vnc) idempotently for the session.

    Raises:
        RuntimeError: If the operation fails at runtime.
    """
    display = _validated_display(session.get("display"))
    width = int(session.get("screen_width") or _DEFAULT_SCREEN_WIDTH)
    height = int(session.get("screen_height") or _DEFAULT_SCREEN_HEIGHT)
    depth = int(session.get("color_depth") or _DEFAULT_COLOR_DEPTH)
    if not _display_up(display):
        subprocess.Popen(  # noqa: S603
            [  # noqa: S607
                "Xvfb",
                display,
                "-screen",
                "0",
                f"{width}x{height}x{depth}",
                "-nolisten",
                "tcp",
            ],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + _SESSION_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _display_up(display):
                break
            time.sleep(_SESSION_POLL_SECONDS)
        else:
            raise RuntimeError(f"Xvfb did not come up on {display}")
        # A minimal window manager helps toolkits place windows; absence
        # must not fail the session (Tk renders without one).
        _spawn_optional(["fluxbox"], _display_env(display))
    if session.get("enable_vnc"):
        port = int(session.get("vnc_port") or _DEFAULT_VNC_PORT)
        _spawn_optional(
            [
                "x11vnc",
                "-display",
                display,
                "-rfbport",
                str(port),
                "-forever",
                "-nopw",
                "-quiet",
                "-bg",
            ],
            _display_env(display),
        )


def _spawn_optional(cmd: list[str], env: dict[str, str]) -> None:
    """Best-effort background spawn; a missing binary is non-fatal."""
    try:
        subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return


def _write_session_state(*, display: str, pid: int) -> None:
    """Record the launched app's pid so later actions can verify it lives."""
    path = Path(_SESSION_STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"display": display, "pid": pid}), encoding="utf-8")


def _read_session_pid() -> int | None:
    """Return the recorded launched-app pid, or None when unset.

    Returns:
        The resulting ``int``, or ``None`` when unavailable.
    """
    path = Path(_SESSION_STATE_PATH)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    pid = data.get("pid")
    return int(pid) if isinstance(pid, int) else None


def _pid_alive(pid: int) -> bool:
    """Return True when *pid* names a live process.

    Returns:
        ``True`` if the operation succeeds, ``False`` otherwise.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _app_running() -> bool:
    """Return True when a launched GUI application is still alive.

    Returns:
        ``True`` if the operation succeeds, ``False`` otherwise.
    """
    pid = _read_session_pid()
    return pid is not None and _pid_alive(pid)


_APP_NOT_RUNNING_ENVELOPE: Final[dict[str, str]] = {
    "status": "error",
    "error_type": "DesktopAppNotRunningError",
    "message": "No GUI application is running",
}

_INPUT_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"click", "type", "key", "scroll"},
)


def _run_xdotool(args: list[str], env: dict[str, str]) -> None:
    """Run an xdotool command, raising on a non-zero exit.

    Raises:
        RuntimeError: If the operation fails at runtime.
    """
    completed = subprocess.run(  # noqa: S603
        ["xdotool", *args],  # noqa: S607
        capture_output=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"xdotool {args[0]} failed")


def _launch(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Launch.

    Returns:
        Mapping from ``str`` to ``Any``.

    Raises:
        ValueError: If an argument fails domain validation.
        RuntimeError: If the operation fails at runtime.
    """
    command = payload.get("app_command")
    if not command:
        raise ValueError("app_command required for launch")
    proc = subprocess.Popen(  # noqa: S603
        ["bash", "-c", command],  # noqa: S607
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    display = env["DISPLAY"]
    timeout = float(
        payload.get("launch_timeout_seconds") or _SESSION_START_TIMEOUT_SECONDS,
    )
    deadline = time.monotonic() + timeout
    window_seen = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("application exited before a window appeared")
        if _has_window(env):
            window_seen = True
            break
        time.sleep(_LAUNCH_POLL_SECONDS)
    if not window_seen:
        proc.terminate()
        # Reap the child so a warm container does not accumulate
        # lingering processes; escalate to kill if it ignores SIGTERM.
        try:
            proc.wait(timeout=_SUBPROCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=_SUBPROCESS_TIMEOUT_SECONDS)
        raise RuntimeError("timed out waiting for application window")
    _write_session_state(display=display, pid=proc.pid)
    return {
        "display": display,
        "pid": proc.pid,
        "screen_width": int(
            payload.get("session", {}).get("screen_width") or _DEFAULT_SCREEN_WIDTH,
        ),
        "screen_height": int(
            payload.get("session", {}).get("screen_height") or _DEFAULT_SCREEN_HEIGHT,
        ),
    }


def _has_window(env: dict[str, str]) -> bool:
    """Return True when at least one mapped top-level window exists.

    Returns:
        ``True`` when the predicate holds, ``False`` otherwise.
    """
    completed = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--name", ""],  # noqa: S607
        capture_output=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        env=env,
        check=False,
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def _click(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Click.

    Returns:
        Mapping from ``str`` to ``Any``.
    """
    x = int(payload["x"])
    y = int(payload["y"])
    button = int(payload.get("button") or 1)
    _run_xdotool(["mousemove", "--sync", str(x), str(y)], env)
    repeat = ["--repeat", "2"] if payload.get("double") else []
    _run_xdotool(["click", *repeat, str(button)], env)
    return {"action": "click", "detail": f"button {button} at ({x}, {y})"}


def _type(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Type.

    Returns:
        Mapping from ``str`` to ``Any``.
    """
    text = str(payload.get("text") or "")
    _run_xdotool(["type", "--clearmodifiers", "--", text], env)
    return {"action": "type", "detail": f"{len(text)} chars"}


def _key(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Key.

    Returns:
        Mapping from ``str`` to ``Any``.
    """
    keys = str(payload["keys"])
    _run_xdotool(["key", "--clearmodifiers", keys], env)
    return {"action": "key", "detail": keys}


def _scroll(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Scroll.

    Returns:
        Mapping from ``str`` to ``Any``.
    """
    direction = str(payload.get("direction") or "down")
    amount = int(payload.get("amount") or 1)
    button = _SCROLL_BUTTON_UP if direction == "up" else _SCROLL_BUTTON_DOWN
    _run_xdotool(["click", "--repeat", str(amount), str(button)], env)
    return {"action": "scroll", "detail": f"{direction} x{amount}"}


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Parse width/height from a PNG IHDR header.

    Returns:
        Tuple ``(int, int)``.
    """
    width = int.from_bytes(
        data[_PNG_IHDR_WIDTH_OFFSET : _PNG_IHDR_WIDTH_OFFSET + _UINT32_BYTES],
        "big",
    )
    height = int.from_bytes(
        data[_PNG_IHDR_HEIGHT_OFFSET : _PNG_IHDR_HEIGHT_OFFSET + _UINT32_BYTES],
        "big",
    )
    return width, height


def _screenshot(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Screenshot.

    Returns:
        Mapping from ``str`` to ``Any``.

    Raises:
        RuntimeError: If the operation fails at runtime.
    """
    out = _validated_sandbox_path(payload["screenshot_path"], field="screenshot_path")
    out.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(  # noqa: S603
        ["scrot", "--overwrite", str(out)],  # noqa: S607
        capture_output=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        env=env,
        check=False,
    )
    if completed.returncode != 0 or not out.exists():
        raise RuntimeError("scrot failed to capture the display")
    data = out.read_bytes()
    width, height = _png_dimensions(data)
    return {
        "saved_path": str(out),
        "width": width,
        "height": height,
        "file_size_bytes": out.stat().st_size,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


_DISPATCH = {
    "launch": _launch,
    "click": _click,
    "type": _type,
    "key": _key,
    "scroll": _scroll,
    "screenshot": _screenshot,
}


def _dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch.

    Returns:
        Mapping from ``str`` to ``Any``.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    session = payload.get("session") or {}
    _start_session(session)
    display = _validated_display(session.get("display"))
    env = _display_env(display)
    operation = payload["operation"]
    handler = _DISPATCH.get(operation)
    if handler is None:
        raise ValueError(f"unknown operation: {operation!r}")
    if operation in _INPUT_OPERATIONS and not _app_running():
        return dict(_APP_NOT_RUNNING_ENVELOPE)
    result = handler(payload, env)
    settle = float(payload.get("settle_delay_seconds") or 0.0)
    if settle > 0.0:
        time.sleep(settle)
    return {"status": "ok", "result": result}


def main() -> int:
    """Main.

    Returns:
        Result of type ``int``.
    """
    raw = os.environ.get("DESKTOP_TOOL_ARGS_JSON")
    if not raw:
        sys.stdout.write(
            json.dumps(
                {
                    "status": "error",
                    "error_type": "DesktopArgumentError",
                    "message": "DESKTOP_TOOL_ARGS_JSON env var is required",
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
                    "error_type": "DesktopArgumentError",
                    "message": f"invalid JSON args: {exc.msg}",
                },
            ),
        )
        return 2
    try:
        result = _dispatch(payload)
    except Exception as exc:
        reraise_critical(exc)
        # Redact the raw message: str(exc) can carry filesystem paths,
        # env vars, or window content. Emit only the class name plus a
        # static generic message so the host has a stable shape.
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
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
