"""Prompt eval: memory consolidation prompt contract."""

import inspect

import pytest


@pytest.mark.unit
class TestMemoryConsolidationPromptContract:
    """Guard rails for the abstractive memory consolidation surface."""

    def test_config_pins_explicit_temperature(self) -> None:
        """Consolidation must bind ``temperature`` to an explicit literal.

        The previous ``"temperature" in source`` check passed for any
        occurrence of the word -- including docstrings, comments, or
        an unrelated parameter list. We instead require that the
        module contains a ``temperature=<literal>`` binding so the
        temperature is actually pinned in code.

        Note: consolidation intentionally uses a non-zero default
        (``0.3``) for summarization creativity, so this test does
        NOT require ``temperature=0.0``. The rubric grader and the
        re-ranker pin zero; consolidation does not.
        """
        import re

        from synthorg.memory.consolidation import abstractive

        source = inspect.getsource(abstractive)
        # Match either a direct default-value binding (``temperature=0.3``,
        # ``temperature: float = 0.3``) OR a module-level Final constant
        # of the form ``_DEFAULT_TEMPERATURE: Final[float] = 0.3`` that
        # the function default references. Either pattern pins the value
        # to a single explicit literal so drift is detectable.
        assert re.search(
            r"(temperature\s*(?::\s*[A-Za-z_][\w\[\], ]*)?\s*=\s*[-+]?[\d.]+"
            r"|_DEFAULT_TEMPERATURE\s*:\s*Final\[float\]\s*=\s*[-+]?[\d.]+)",
            source,
        ), (
            "abstractive consolidation must bind temperature to an "
            "explicit numeric literal so drift is detectable"
        )


@pytest.mark.unit
class TestLLMSynthesisConfigBinding:
    """``LLMSynthesisOp`` must thread its config sampling into the call."""

    def test_completion_config_binds_temperature_and_top_p(self) -> None:
        """The synthesis op's CompletionConfig reflects config sampling.

        Proves the config fields actually drive the call-time sampling
        rather than being silently dropped: a custom ``temperature`` /
        ``top_p`` on ``LLMConsolidationConfig`` must surface on the
        built ``CompletionConfig``.
        """
        from synthorg.memory.consolidation.config import LLMConsolidationConfig
        from synthorg.memory.consolidation.llm_op import LLMSynthesisOp
        from synthorg.memory.consolidation.provider_port import CompletionPort
        from synthorg.memory.protocol import MemoryBackend
        from tests._shared import mock_of

        cfg = LLMConsolidationConfig(temperature=0.7, top_p=0.5)
        op = LLMSynthesisOp(
            backend=mock_of[MemoryBackend](),
            provider=mock_of[CompletionPort](),
            model="test-small-001",
            config=cfg,
        )
        assert op._completion_config.temperature == 0.7
        assert op._completion_config.top_p == 0.5
