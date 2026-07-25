"""Resolve a Bash that can execute a script by its native path.

``shutil.which("bash")`` is not sufficient on Windows. The WSL launcher lives
at ``C:\\Windows\\System32\\bash.exe`` and usually precedes Git Bash on PATH,
but it runs inside the Linux VM: handed a Windows path it strips the
backslashes and exits 127. A hook test that resolved it would fail on a
machine where the hook itself is perfectly fine, which is the opposite of what
these tests are for.

So the WSL launcher is rejected and Git Bash is located relative to the ``git``
already on PATH, which finds it wherever Git for Windows was installed. When
neither is present the tests skip, exactly as they already do on a machine with
no Bash at all.
"""

import shutil
from pathlib import Path

#: The subdirectories Git for Windows puts Bash in, relative to its install
#: root. ``bin`` is the wrapper, ``usr/bin`` the MSYS binary behind it.
_BASH_SUBPATHS: tuple[str, ...] = ("bin/bash.exe", "usr/bin/bash.exe")

_WSL_LAUNCHER_DIR = "system32"


def _is_wsl_launcher(candidate: Path) -> bool:
    """Whether *candidate* is the WSL launcher rather than a real Bash.

    Returns:
        ``True`` for the ``System32\\bash.exe`` launcher.
    """
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    return any(part.lower() == _WSL_LAUNCHER_DIR for part in resolved.parts)


def _git_bash_candidates() -> tuple[Path, ...]:
    """Bash paths derived from the Git install that owns the ``git`` on PATH.

    Returns:
        The candidate paths, empty when Git is not installed.
    """
    git = shutil.which("git")
    if git is None:
        return ()
    # Git lives in <root>/cmd/git.exe or <root>/bin/git.exe; Bash is a sibling
    # of that directory, so the install root is two levels up either way.
    root = Path(git).resolve().parent.parent
    return tuple(root / sub for sub in _BASH_SUBPATHS)


def resolve_bash() -> str | None:
    """Return a Bash that can run a script given a native path.

    Returns:
        The executable path, or ``None`` when no usable Bash is installed.
    """
    found = shutil.which("bash")
    if found is not None and not _is_wsl_launcher(Path(found)):
        return found
    for candidate in _git_bash_candidates():
        if candidate.is_file():
            return str(candidate)
    return None
