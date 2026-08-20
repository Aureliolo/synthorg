"""The health surface must name the build that is actually serving.

``synthorg.__version__`` is the last released version. A live run on
``v0.9.4-dev.143`` had the health dialog headline "BACKEND VERSION 0.9.3", the
previous release, which names nothing the operator is running.
"""

import pytest

from synthorg import __version__
from synthorg.core.build_identity import IMAGE_TAG_ENV, running_version

pytestmark = pytest.mark.unit


class TestRunningVersion:
    def test_the_launcher_s_tag_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(IMAGE_TAG_ENV, "v0.9.4-dev.143")

        assert running_version() == "v0.9.4-dev.143"

    def test_no_tag_falls_back_to_the_source_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stack built from a worktree chose no tag, and source is the truth."""
        monkeypatch.delenv(IMAGE_TAG_ENV, raising=False)

        assert running_version() == __version__

    def test_a_blank_tag_is_not_a_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Compose interpolates an unset variable to the empty string."""
        monkeypatch.setenv(IMAGE_TAG_ENV, "   ")

        assert running_version() == __version__
