# module-kind: code
"""Log-stream draining and marker parsing for fine-tune containers.

The stage runner (``fine_tune_docker_runner``) communicates with its
ephemeral containers exclusively through structured stdout markers
(``PROGRESS:<fraction>`` drives the WS progress pipeline,
``ERROR:<message>`` captures the failure detail). This module owns the
stream-to-line reassembly and marker dispatch so the runner keeps only
container lifecycle concerns.
"""

from typing import Final

import aiodocker.containers

from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.embedding.cancellation import ProgressCallback
from synthorg.memory.errors import FineTuneStageExecutionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.fine_tune import (
    FINE_TUNE_CONTAINER_FAILED,
    FINE_TUNE_MARKER_DISCARDED,
    FINE_TUNE_PROGRESS_CALLBACK_FAILED,
)

logger = get_logger(__name__)

_MARKER_PROGRESS: Final[str] = "PROGRESS:"
_MARKER_ERROR: Final[str] = "ERROR:"
_SHORT_ID_LEN: Final[int] = 12

#: Ceiling on what one ``ERROR:`` line contributes to the operator-visible
#: failure message. The marker protocol is ours, but the streams it is read
#: from are shared with every library in the container, so any line beginning
#: with the prefix is collected whether the runner wrote it or not. The
#: container also holds the organisation's documents, so an unbounded payload
#: is both an error message nobody can read and a way for third-party output
#: to carry that text out. Truncated rather than dropped: a genuine traceback
#: line is worth keeping even when something else made it long.
_MAX_ERROR_PAYLOAD_CHARS: Final[int] = 500

#: Ceiling on how many such lines one stage contributes. The per-line cap
#: bounds a line; a stage runs for hours against streams anything in the
#: container can write to, so without this the collected list and the message
#: built from it grow without limit for exactly the reasons above. The FIRST
#: lines are kept rather than the last: the failure that ended the stage is
#: usually the one that started the cascade.
_MAX_ERROR_LINES: Final[int] = 20


async def stream_markers_until_exit(
    container: aiodocker.containers.DockerContainer,
    progress_callback: ProgressCallback | None,
    error_lines: list[str],
) -> int:
    """Drain marker output, then return the container's exit code.

    Returns:
        The container's exit status code.

    Raises:
        FineTuneStageExecutionError: When the log stream or the exit
            wait fails mid-stage (daemon restart, connection reset), so
            callers see the documented error type instead of a raw
            transport exception.
    """
    # The log stream yields decoded transport chunks, not lines: one
    # chunk can carry several lines and a line can span chunks, so
    # marker parsing buffers and splits on newlines itself.
    buffer = ""
    try:
        async for raw in container.log(stdout=True, stderr=True, follow=True):
            buffer += raw
            while "\n" in buffer:
                line, _, buffer = buffer.partition("\n")
                handle_marker_line(line.strip(), progress_callback, error_lines)
        if buffer.strip():
            handle_marker_line(buffer.strip(), progress_callback, error_lines)
        result = await container.wait()
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            FINE_TUNE_CONTAINER_FAILED,
            phase="log_stream",
            container_id=container.id[:_SHORT_ID_LEN],
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"container log stream failed mid-stage: {safe_error_description(exc)}"
        raise FineTuneStageExecutionError(msg) from exc
    return int(result.get("StatusCode", -1))


def handle_marker_line(
    line: str,
    progress_callback: ProgressCallback | None,
    error_lines: list[str],
) -> None:
    """Dispatch one marker line from the stage container.

    Read from the combined stdout and stderr stream, so the prefixes are a
    convention the runner follows rather than a channel only it can write to:
    anything in the container may emit a line that starts with one. Both
    branches are therefore written to survive an impostor. A malformed
    ``PROGRESS:`` payload is discarded as noise, and an ``ERROR:`` payload is
    truncated before it reaches the operator's failure message.
    """
    if line.startswith(_MARKER_PROGRESS):
        if progress_callback is None:
            return
        fraction_text = line.removeprefix(_MARKER_PROGRESS).strip()
        try:
            fraction = float(fraction_text)
        except ValueError:
            # A malformed fraction is log noise, not a failure.
            logger.debug(
                FINE_TUNE_MARKER_DISCARDED,
                marker=_MARKER_PROGRESS,
                payload=fraction_text[:_SHORT_ID_LEN],
            )
            return
        # A raising callback is an application bug (e.g. in the WS
        # progress pipeline), not a Docker transport failure: contain it
        # here so it is neither folded into the caller's stream-failure
        # error path nor allowed to abort the stage supervision.
        try:
            progress_callback(fraction)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                FINE_TUNE_PROGRESS_CALLBACK_FAILED,
                marker=_MARKER_PROGRESS,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    elif line.startswith(_MARKER_ERROR):
        if len(error_lines) >= _MAX_ERROR_LINES:
            return
        payload = line.removeprefix(_MARKER_ERROR).strip()
        error_lines.append(payload[:_MAX_ERROR_PAYLOAD_CHARS])


async def drain_probe_output(
    container: aiodocker.containers.DockerContainer,
) -> str:
    """Collect the probe container's full output until exit.

    Returns:
        The concatenated stdout/stderr stream.
    """
    lines = [raw async for raw in container.log(stdout=True, stderr=True, follow=True)]
    await container.wait()
    return "".join(lines)
