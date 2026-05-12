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
