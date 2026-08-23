"""Tests for the fine-tune pipeline container entrypoint."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune_runner import (
    _load_config,
    _make_progress_printer,
    _run,
)
from synthorg.memory.errors import FineTuneDependencyError
from tests._shared import module_double, torch_double

pytestmark = pytest.mark.unit

_CONFIG_ENV = "SYNTHORG_FINE_TUNE_STAGE_CONFIG"
_PROBE_ENV = "SYNTHORG_FINE_TUNE_PROBE"
_DISPATCH = "synthorg.memory.embedding.fine_tune_stage_dispatch.dispatch_stage"


@pytest.fixture(autouse=True)
def _clean_runner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_CONFIG_ENV, raising=False)
    monkeypatch.delenv(_PROBE_ENV, raising=False)


def _set_config(monkeypatch: pytest.MonkeyPatch, config: dict[str, object]) -> None:
    monkeypatch.setenv(_CONFIG_ENV, json.dumps(config))


class TestLoadConfig:
    """Inline env-var config loading and validation."""

    def test_missing_env_returns_none(self) -> None:
        assert _load_config() is None

    def test_blank_env_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_CONFIG_ENV, "   ")
        assert _load_config() is None

    def test_invalid_json_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_CONFIG_ENV, "{invalid")
        assert _load_config() is None

    def test_non_object_json_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_CONFIG_ENV, "[1, 2, 3]")
        assert _load_config() is None

    def test_valid_config_returns_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_config(monkeypatch, {"stage": "training"})
        assert _load_config() == {"stage": "training"}


class TestProgressPrinter:
    """Throttled ``PROGRESS:`` marker emission."""

    def test_emits_first_and_final(self, capsys: pytest.CaptureFixture[str]) -> None:
        printer = _make_progress_printer()
        printer(0.0)
        printer(0.001)
        printer(0.5)
        printer(1.0)
        out = capsys.readouterr().out
        assert "PROGRESS:0.0000" in out
        assert "PROGRESS:0.0010" not in out
        assert "PROGRESS:0.5000" in out
        assert "PROGRESS:1.0000" in out


class TestRun:
    """Entrypoint ``_run()`` behaviour."""

    def test_missing_config_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run() == 1
        assert _CONFIG_ENV in capsys.readouterr().err

    def test_unknown_stage_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_config(monkeypatch, {"stage": "not_a_stage"})
        assert _run() == 1

    @pytest.mark.parametrize(
        "stage",
        ["idle", "generating_data", "deploying", ""],
        ids=["idle", "generating_data", "deploying", "empty"],
    )
    def test_non_container_stage_returns_1(
        self, monkeypatch: pytest.MonkeyPatch, stage: str
    ) -> None:
        _set_config(monkeypatch, {"stage": stage})
        assert _run() == 1

    def test_missing_stage_key_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_config(monkeypatch, {"not_stage": "value"})
        assert _run() == 1

    def test_successful_stage_returns_0(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _set_config(
            monkeypatch,
            {
                "stage": "training",
                "training_data_path": "/data/t.jsonl",
                "base_model": "test-model",
                "output_dir": "/data/fine-tune",
            },
        )
        mock_dispatch = AsyncMock()
        with patch(_DISPATCH, mock_dispatch):
            assert _run() == 0
        mock_dispatch.assert_awaited_once()
        captured = capsys.readouterr()
        assert "STAGE_START:training" in captured.out
        assert "STAGE_COMPLETE:training" in captured.out

    def test_stage_exception_returns_1_with_error_marker(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _set_config(monkeypatch, {"stage": "training"})
        mock_dispatch = AsyncMock(side_effect=ValueError("bad config"))
        with patch(_DISPATCH, mock_dispatch):
            assert _run() == 1
        assert "ERROR: training failed" in capsys.readouterr().err

    def test_memory_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_config(monkeypatch, {"stage": "training"})
        mock_dispatch = AsyncMock(side_effect=MemoryError("OOM"))
        with patch(_DISPATCH, mock_dispatch), pytest.raises(MemoryError):
            _run()

    def test_recursion_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_config(monkeypatch, {"stage": "training"})
        mock_dispatch = AsyncMock(side_effect=RecursionError())
        with patch(_DISPATCH, mock_dispatch), pytest.raises(RecursionError):
            _run()

    def test_sigterm_handler_cancels_token_and_is_restored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SIGTERM (docker stop) fires the cooperative token, and the
        previous handler is reinstated after the run."""
        import signal

        _set_config(monkeypatch, {"stage": "training"})
        previous = signal.getsignal(signal.SIGTERM)
        tokens: list[object] = []

        async def _capture(
            _stage: object,
            _config: object,
            token: object,
            *,
            progress_callback: object,
        ) -> None:
            del progress_callback
            tokens.append(token)
            # Fire the installed handler exactly as the signal module
            # would on a real SIGTERM delivery.
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler)
            handler(signal.SIGTERM, None)

        with patch(_DISPATCH, _capture):
            assert _run() == 0
        assert len(tokens) == 1
        token = tokens[0]
        assert isinstance(token, CancellationToken)
        assert token.is_cancelled is True
        assert signal.getsignal(signal.SIGTERM) == previous


class TestProbeMode:
    """``SYNTHORG_FINE_TUNE_PROBE=1`` readiness probe."""

    def test_probe_fail_when_ml_deps_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without the torch extras the probe prints PROBE_FAIL and exits 1.

        The guard is patched rather than left to the ambient environment: a
        developer machine or CI job that HAS the extra installed would
        otherwise take the success branch and the assertion would flip on
        what happens to be on the box.
        """
        monkeypatch.setenv(_PROBE_ENV, "1")
        with patch(
            "synthorg.memory.embedding.fine_tune._import_torch",
            side_effect=FineTuneDependencyError("torch is not installed"),
        ):
            assert _run() == 1
        out = capsys.readouterr().out
        assert any(line.startswith("PROBE_FAIL") for line in out.splitlines())

    def test_probe_fail_when_only_the_trainer_half_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A package-only install is not a ready deployment.

        ``datasets`` and ``accelerate`` are absent from sentence-transformers'
        own dependency list, so an install that pinned the bare package
        imports fine and still cannot train. The probe has to fail here or the
        run dies two stages later.
        """
        monkeypatch.setenv(_PROBE_ENV, "1")

        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_torch",
                return_value=torch_double(cuda=None),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                return_value=module_double("sentence_transformers"),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune.import_trainer_api",
                side_effect=FineTuneDependencyError("datasets is not installed"),
            ),
        ):
            assert _run() == 1
        out = capsys.readouterr().out
        # Named, not merely failed: with a `None` CUDA namespace the GPU
        # branch below fails too, so a bare PROBE_FAIL assertion would pass
        # on a probe that never asked the question this test is about.
        assert any(
            line.startswith("PROBE_FAIL") and "datasets" in line
            for line in out.splitlines()
        )

    def test_probe_ok_reports_gpu(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(_PROBE_ENV, "1")

        class _Props:
            name = "Example GPU 90"
            total_memory = 24 * 1024**3

        class _Cuda:
            @staticmethod
            def is_available() -> bool:
                return True

            @staticmethod
            def get_device_properties(index: int) -> _Props:
                return _Props()

        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_torch",
                return_value=torch_double(cuda=_Cuda()),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                return_value=module_double("sentence_transformers"),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune.import_trainer_api",
                return_value=object(),
            ),
        ):
            assert _run() == 0
        assert "PROBE_OK gpu=Example GPU 90 vram_gb=24.0" in capsys.readouterr().out

    def test_probe_ok_without_gpu(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(_PROBE_ENV, "1")

        class _Cuda:
            @staticmethod
            def is_available() -> bool:
                return False

        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_torch",
                return_value=torch_double(cuda=_Cuda()),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                return_value=module_double("sentence_transformers"),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune.import_trainer_api",
                return_value=object(),
            ),
        ):
            assert _run() == 0
        assert "PROBE_OK gpu=none vram_gb=0.0" in capsys.readouterr().out

    def test_probe_fail_when_cuda_inspection_breaks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A broken CUDA runtime yields PROBE_FAIL, not a crash."""
        monkeypatch.setenv(_PROBE_ENV, "1")

        class _Cuda:
            @staticmethod
            def is_available() -> bool:
                msg = "driver mismatch"
                raise RuntimeError(msg)

        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_torch",
                return_value=torch_double(cuda=_Cuda()),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                return_value=module_double("sentence_transformers"),
            ),
            patch(
                "synthorg.memory.embedding.fine_tune.import_trainer_api",
                return_value=object(),
            ),
        ):
            assert _run() == 1
        out = capsys.readouterr().out
        assert "PROBE_FAIL CUDA inspection failed" in out
