"""Resolve a Bash that can execute a script by its native path.

``shutil.which("bash")`` is not sufficient on Windows. The WSL launcher lives
at ``C:\\Windows\\System32\\bash.exe`` and usually precedes Git Bash on PATH,
but it runs inside the Linux VM: handed a Windows path it strips the
backslashes and exits 127. A hook test that resolved it would fail on a
machine where the hook itself is perfectly fine, which is the opposite of what
these tests are for.

So the WSL launcher is rejected and Git Bash is looked for by its usual
install locations. When neither is present the tests skip, exactly as they
already do on a machine with no Bash at all.
"""

import os
import shutil
from pathlib import Path

#: Where Git for Windows puts Bash. Checked in order when PATH only offers the
#: WSL launcher.
_GIT_BASH_CANDIDATES: tuple[Path, ...] = (
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/usr/bin/bash.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Git/bin/bash.exe",
)


def _is_wsl_launcher(candidate: Path) -> bool:
    """Whether *candidate* is the WSL launcher rather than a real Bash.

    Returns:
        ``True`` for the ``System32\\bash.exe`` launcher.
    """
    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    try:
        return candidate.resolve().is_relative_to(system_root / "System32")
    except OSError:
        return False


def resolve_bash() -> str | None:
    """Return a Bash that can run a script given a native path.

    Returns:
        The executable path, or ``None`` when no usable Bash is installed.
    """
    found = shutil.which("bash")
    if found is not None and not _is_wsl_launcher(Path(found)):
        return found
    for candidate in _GIT_BASH_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None
