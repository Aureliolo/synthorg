"""Reclaim test containers a killed run left behind.

The shared-container fixtures stop and remove their container from a
``finally`` when the last worker releases it. That covers a run that ends;
it cannot cover one that is killed, and a killed run is the ordinary case
here: a pytest timeout, an xdist worker crash, a Ctrl-C, or a helper script
that kills the process tree. The refcount is then never decremented, the
state file dies with the pytest tmp directory that held it, and the
container is left running with nothing able to find it again.

One developer machine had accumulated 122 of them, each pinning its image
and its anonymous volume.

This sweep is deliberately narrow. It removes containers that are
**already exited** and carry testcontainers' own label, so it cannot touch
a container another run is using, and it cannot touch anything that is not
a test container. Running containers are left alone precisely because
"running" and "abandoned" are indistinguishable from outside the process
that created it, and killing the wrong one costs somebody their test run.
"""

import subprocess

_LABEL = "org.testcontainers=true"
_TIMEOUT_SECONDS = 30


def _docker(*args: str) -> str | None:
    """Run a docker command, returning stdout or ``None`` on any failure.

    Best-effort by design: this is housekeeping around a test run, and a
    machine with no Docker, a stopped daemon or a permissions problem
    should skip it rather than fail collection.

    Returns:
        Stdout, or ``None`` when docker could not be run.
    """
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed argv, no shell
            ["docker", *args],  # noqa: S607 -- resolved from PATH by design
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def reclaim_exited_testcontainers() -> tuple[str, ...]:
    """Remove exited testcontainers-labelled containers.

    Returns:
        The container ids removed, empty when there was nothing to do or
        docker was unavailable.
    """
    listed = _docker(
        "ps",
        "--all",
        "--quiet",
        "--filter",
        f"label={_LABEL}",
        "--filter",
        "status=exited",
    )
    if not listed:
        return ()

    # ``-v`` takes the anonymous volume with it. Removing the container alone
    # converts a visible leak into one that no container listing will show.
    return tuple(
        container_id
        for container_id in listed.split()
        if _docker("rm", "-v", container_id) is not None
    )


__all__ = ["reclaim_exited_testcontainers"]
