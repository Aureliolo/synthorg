"""A git command a fixture depends on fails with git's own words.

``subprocess.run(check=True)`` raises ``CalledProcessError``, whose message
is the argv and the exit code and nothing else. Two setups failed exactly
that way in one run (``['git', 'commit', ...] -> 128``,
``['git', 'add', '.'] -> 128``) and left nothing to diagnose from: the
working tree, the index and the identity all fail the same way through that
message. git had written the answer to stderr and the fixture dropped it.
"""

from pathlib import Path

import pytest

from tests.unit.tools.git.conftest import GitFixtureError, _run_git, _run_git_output

pytestmark = pytest.mark.unit


class TestGitFixtureDiagnostics:
    def test_a_failed_command_carries_git_stderr(self, tmp_path: Path) -> None:
        # Not a repository: git says so, and the fixture must repeat it.
        with pytest.raises(GitFixtureError) as caught:
            _run_git(["log"], tmp_path)

        message = str(caught.value)
        assert "git log exited" in message
        assert "not a git repository" in message.casefold()

    def test_the_failure_names_the_directory_it_ran_in(self, tmp_path: Path) -> None:
        # Under xdist every worker has its own tmp_path, so which tree the
        # command ran in is what separates one worker's fault from another's.
        with pytest.raises(GitFixtureError) as caught:
            _run_git_output(["rev-parse", "HEAD"], tmp_path)

        assert str(tmp_path) in str(caught.value)

    def test_a_successful_command_still_returns_its_output(
        self, git_repo: Path
    ) -> None:
        assert len(_run_git_output(["rev-parse", "HEAD"], git_repo)) == 40
