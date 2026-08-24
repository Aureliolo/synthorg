# module-kind: tests
"""The series-drift report names pins the upstream index has moved past."""

import gzip
import io
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final
from unittest.mock import patch

import pytest
from scripts.report_apko_series_drift import main

pytestmark = pytest.mark.unit

_WOLFI: Final[str] = "https://packages.wolfi.dev/os"
_MANIFEST: Final[str] = "docker/sandbox/apko.yaml"


def _manifest(root: Path, packages: list[str], *, repository: str = _WOLFI) -> None:
    """Write an apko manifest naming `packages` from `repository`."""
    body = "\n".join(f"    - {name}" for name in packages)
    path = root / _MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"contents:\n  repositories:\n    - {repository}\n  packages:\n{body}\n",
        encoding="utf-8",
        newline="\n",
    )


def _index(names: list[str]) -> bytes:
    """Return an APKINDEX.tar.gz publishing `names`."""
    payload = "".join(f"P:{name}\nV:1-r0\n\n" for name in names).encode("utf-8")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo("APKINDEX")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return gzip.compress(raw.getvalue())


class _Response:
    """A urlopen stand-in: a context manager over a fixed body."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        """Return the body, honouring the caller's size cap."""
        return self._body if size < 0 else self._body[:size]


@contextmanager
def _served(names: list[str]) -> Iterator[None]:
    """Patch the index fetch to serve `names`."""
    with patch(
        "scripts.report_apko_series_drift.urllib.request.urlopen",
        return_value=_Response(_index(names)),
    ):
        yield


class TestDrift:
    """A later series of the same shape is drift."""

    def test_a_newer_series_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _manifest(tmp_path, ["wolfi-baselayout", "nodejs-24"])

        with _served(["nodejs-24", "nodejs-25", "nodejs-26"]):
            assert main(["--repo-root", str(tmp_path)]) == 1

        out = capsys.readouterr().out
        assert "`nodejs-24`" in out
        assert "`nodejs-25`, `nodejs-26`" in out
        assert _MANIFEST in out

    def test_a_series_inside_the_name_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A skeleton is the whole name, not a prefix."""
        _manifest(tmp_path, ["postgresql-18-client"])

        with _served(["postgresql-18-client", "postgresql-19-client"]):
            assert main(["--repo-root", str(tmp_path)]) == 1

        assert "`postgresql-19-client`" in capsys.readouterr().out

    def test_the_newest_series_is_not_drift(self, tmp_path: Path) -> None:
        _manifest(tmp_path, ["npm-12"])

        with _served(["npm-11", "npm-12", "npm-async", "npm-doc"]):
            assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_different_component_count_is_not_a_successor(
        self, tmp_path: Path
    ) -> None:
        """`py3.11-pip` is another interpreter line, not a newer `py3-pip`."""
        _manifest(tmp_path, ["py3-pip"])

        with _served(["py3-pip", "py3.11-pip", "py3.14-pip"]):
            assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_dotted_series_compares_numerically(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`2.9` is older than `2.43`, which string order would reverse."""
        _manifest(tmp_path, ["glibc-2.9"])

        with _served(["glibc-2.9", "glibc-2.43"]):
            assert main(["--repo-root", str(tmp_path)]) == 1

        assert "`glibc-2.43`" in capsys.readouterr().out

    def test_a_manifest_from_another_repository_is_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A pin judged against the wrong index is worse than no answer."""
        _manifest(tmp_path, ["nodejs-24"], repository="https://example.invalid/os")

        with _served(["nodejs-26"]):
            assert main(["--repo-root", str(tmp_path)]) == 2

        assert "no Wolfi-backed manifest pins a series" in capsys.readouterr().err


class TestUntrustworthyScan:
    """A scan that cannot be trusted exits 2 rather than reporting no drift."""

    def test_no_manifest_at_all_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "nothing to watch" in capsys.readouterr().err

    def test_an_unreachable_index_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _manifest(tmp_path, ["nodejs-24"])

        with patch(
            "scripts.report_apko_series_drift.urllib.request.urlopen",
            side_effect=OSError("mirror down"),
        ):
            assert main(["--repo-root", str(tmp_path)]) == 2

        assert "could not be fetched" in capsys.readouterr().err

    def test_an_index_holding_no_names_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _manifest(tmp_path, ["nodejs-24"])

        with _served([]):
            assert main(["--repo-root", str(tmp_path)]) == 2

        assert "no package names" in capsys.readouterr().err

    def test_an_unreadable_manifest_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / _MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("contents: [not, a, mapping]\n", encoding="utf-8", newline="\n")

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "no `contents`" in capsys.readouterr().err
