"""Tests for ``scripts/embed_logfire_token.py``."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "embed_logfire_token.py"


def _load_script() -> ModuleType:
    """Import the embedder script as a module without altering sys.path."""
    spec = importlib.util.spec_from_file_location(
        "embed_logfire_token",
        _SCRIPT_PATH,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def embed_module() -> ModuleType:
    return _load_script()


@pytest.fixture
def fake_target(tmp_path: Path) -> Path:
    """Create a target file containing the sentinel."""
    target = tmp_path / "_embedded_token.py"
    target.write_text(
        'EMBEDDED_LOGFIRE_TOKEN = "__SYNTHORG_LOGFIRE_TOKEN_NOT_EMBEDDED__"\n',
        encoding="utf-8",
    )
    return target


@pytest.mark.unit
class TestEmbed:
    """The library entrypoint ``embed(token, target)``."""

    def test_replaces_sentinel(
        self,
        embed_module: ModuleType,
        fake_target: Path,
    ) -> None:
        embed_module.embed("pylf_v1_real_secret", fake_target)
        new = fake_target.read_text(encoding="utf-8")
        assert "__SYNTHORG_LOGFIRE_TOKEN_NOT_EMBEDDED__" not in new
        assert "pylf_v1_real_secret" in new

    def test_rejects_empty_token(
        self,
        embed_module: ModuleType,
        fake_target: Path,
    ) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            embed_module.embed("", fake_target)
        with pytest.raises(ValueError, match="non-empty"):
            embed_module.embed("   ", fake_target)

    def test_rejects_already_embedded_target(
        self,
        embed_module: ModuleType,
        tmp_path: Path,
    ) -> None:
        """Re-embedding must fail loudly to prevent silently overwriting tokens."""
        target = tmp_path / "_embedded_token.py"
        target.write_text(
            'EMBEDDED_LOGFIRE_TOKEN = "real_token_already_baked"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="sentinel"):
            embed_module.embed("another_token", target)

    def test_replaces_only_first_occurrence(
        self,
        embed_module: ModuleType,
        tmp_path: Path,
    ) -> None:
        """Defensive: even if a docstring contains the sentinel string,
        only the constant assignment is rewritten."""
        target = tmp_path / "_embedded_token.py"
        sentinel = "__SYNTHORG_LOGFIRE_TOKEN_NOT_EMBEDDED__"
        target.write_text(
            (
                f'"""docstring with sentinel: {sentinel}"""\n'
                f'EMBEDDED_LOGFIRE_TOKEN = "{sentinel}"\n'
            ),
            encoding="utf-8",
        )
        embed_module.embed("real_token", target)
        new = target.read_text(encoding="utf-8")
        # Exactly one replacement; the docstring still carries the
        # sentinel literal so future operators can search for the
        # rotation marker without confusing it for the runtime token.
        assert new.count("__SYNTHORG_LOGFIRE_TOKEN_NOT_EMBEDDED__") == 1
        assert "real_token" in new


@pytest.mark.unit
class TestMain:
    """The CLI entrypoint ``main(argv) -> exit code``."""

    def test_success_returns_zero(
        self,
        embed_module: ModuleType,
        fake_target: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(embed_module, "_resolve_target", lambda: fake_target)
        rc = embed_module.main(["embed_logfire_token.py", "real_token"])
        assert rc == 0

    def test_missing_argument_returns_two(
        self,
        embed_module: ModuleType,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = embed_module.main(["embed_logfire_token.py"])
        assert rc == 2
        assert "usage" in capsys.readouterr().err.lower()

    def test_blank_argument_returns_two(
        self,
        embed_module: ModuleType,
    ) -> None:
        rc = embed_module.main(["embed_logfire_token.py", "   "])
        assert rc == 2

    def test_already_embedded_returns_three(
        self,
        embed_module: ModuleType,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "_embedded_token.py"
        target.write_text(
            'EMBEDDED_LOGFIRE_TOKEN = "already_embedded"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(embed_module, "_resolve_target", lambda: target)
        rc = embed_module.main(["embed_logfire_token.py", "another_token"])
        assert rc == 3

    def test_resolve_target_points_at_real_file(
        self,
        embed_module: ModuleType,
    ) -> None:
        """Sanity check: the default target path resolves to the in-repo file."""
        target = embed_module._resolve_target()
        assert target.exists()
        # Match the full repo-relative path so a future rename or stray
        # ``_embedded_token.py`` elsewhere in the tree cannot satisfy
        # this assertion. The embedder MUST land on this exact file.
        expected_relative = Path(
            "src/synthorg/telemetry/reporters/_embedded_token.py",
        )
        assert target.is_absolute()
        assert target.resolve() == (_REPO_ROOT / expected_relative).resolve()
