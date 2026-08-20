# module-kind: code
"""Which build of this product is actually running.

``synthorg.__version__`` is the last RELEASED version: release-please rewrites
it when it cuts a release, so every build made between two releases carries the
earlier number. A live run on ``v0.9.4-dev.143`` had the health dialog headline
"BACKEND VERSION 0.9.3", which is the previous release and names nothing the
operator is running.

Source cannot answer this. The launcher that chose the image can, so it passes
the tag in and this reads it back. An unset value means nothing chose a tag,
which is true of a stack built from a worktree, and there the source version IS
the build's version.
"""

import os
from typing import Final

from synthorg import __version__

#: Set by every shipped launcher that pulls a published backend image: the CLI's
#: compose template (from its own resolved tag) and ``docker/compose.yml``.
IMAGE_TAG_ENV: Final[str] = "SYNTHORG_IMAGE_TAG"


def running_version() -> str:
    """Return the version of the build serving this process.

    Returns:
        The image tag the launcher pulled, or the source version when nothing
        named one.
    """
    # lint-allow: env-read -- the running artefact's identity is a process fact
    # stamped by the launcher, not configuration: it has no DB tier, no default
    # worth overriding, and reading it through the settings resolver would let
    # a write claim the process is a build it is not.
    tag = os.environ.get(IMAGE_TAG_ENV, "").strip()
    return tag or __version__


__all__ = ["IMAGE_TAG_ENV", "running_version"]
