"""A session-free ``aiodocker.Docker`` double.

Neither standard double works for this client. ``mock_of[aiodocker.Docker]``
cannot carry ``containers``, because ``Docker.__init__`` binds it as an
instance attribute rather than declaring it on the class, so ``create_autospec``
never sees it; and a plain stand-in fails the runtime annotation check that
typeguard applies at the sandbox boundary. Constructing the real client opens a
session to a daemon. Subclassing and skipping ``super().__init__()`` is the one
shape that satisfies all three, so it lives here rather than being reinvented
per test module.
"""

from typing import cast

import aiodocker
import aiodocker.containers

__all__ = ["FakeDockerClient"]


class FakeDockerClient(aiodocker.Docker):
    """An ``aiodocker.Docker`` that opens no session and reaches no daemon.

    Subclasses supply whatever ``containers`` surface the test needs and add
    overrides (``version``, ``close``) when the code under test calls them.
    """

    def __init__(self, containers: object) -> None:
        self.containers = cast("aiodocker.containers.DockerContainers", containers)
