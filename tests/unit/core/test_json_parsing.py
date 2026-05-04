"""Tests for ``synthorg.core.json_parsing``."""

import pytest

from synthorg.core.json_parsing import (
    extract_json_array_from_llm_response,
    extract_json_from_llm_response,
)


@pytest.mark.unit
class TestObjectExtractor:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('{"k": "v"}', {"k": "v"}),
            ('  {"k": "v"}  ', {"k": "v"}),
            ('```json\n{"k": "v"}\n```', {"k": "v"}),
            ('```\n{"k": "v"}\n```', {"k": "v"}),
            ('Here is the JSON: {"k": "v"} done.', {"k": "v"}),
        ],
    )
    def test_happy_paths(self, text: str, expected: dict[str, str]) -> None:
        assert extract_json_from_llm_response(text) == expected

    @pytest.mark.parametrize("text", ["", "   ", "not json at all"])
    def test_failures_return_none(self, text: str) -> None:
        assert extract_json_from_llm_response(text) is None

    def test_array_input_rejected(self) -> None:
        """The dict variant returns None when the response is a JSON array."""
        assert extract_json_from_llm_response('["a", "b"]') is None

    def test_array_with_inner_object_does_not_extract_inner(self) -> None:
        """A list of dicts is a wrong-shape parse, not a brace-fallback cue.

        The naive brace-substring fallback would happily return the
        inner ``{"k": 1}`` from ``[{"k": 1}]`` and silently send the
        caller down the wrong code path. The extractor must stop at
        the wrong-top-level-type result and return None.
        """
        assert extract_json_from_llm_response('[{"k": 1}]') is None

    def test_wrong_type_logs_distinct_event(self) -> None:
        """Wrong-top-level shape and decode errors get distinct labels."""
        seen: list[str] = []
        extract_json_from_llm_response("[1, 2]", logger_callback=seen.append)
        assert seen == ["json_wrong_top_level_type"]

    def test_logger_callback_invoked_on_failure(self) -> None:
        seen: list[str] = []

        def _capture(detail: str) -> None:
            seen.append(detail)

        extract_json_from_llm_response("garbage", logger_callback=_capture)
        assert seen == ["json_decode_error"]

    def test_logger_callback_swallows_exceptions(self) -> None:
        """A misbehaving callback must not break the extractor's contract."""

        def _broken(_detail: str) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        # Returns None (failure path) without re-raising the callback's error.
        assert (
            extract_json_from_llm_response("garbage", logger_callback=_broken) is None
        )

    def test_logger_callback_propagates_system_errors(self) -> None:
        """System-level callback errors must NOT be swallowed.

        A naive ``contextlib.suppress(Exception)`` would also absorb
        ``MemoryError`` and ``RecursionError``, masking process-level
        resource exhaustion. The project convention is to re-raise so
        the caller sees the unrecoverable state.
        """

        def _oom(_detail: str) -> None:
            raise MemoryError

        with pytest.raises(MemoryError):
            extract_json_from_llm_response("garbage", logger_callback=_oom)

        def _stack(_detail: str) -> None:
            raise RecursionError

        with pytest.raises(RecursionError):
            extract_json_from_llm_response("garbage", logger_callback=_stack)

    def test_stray_braces_in_prose_do_not_defeat_fallback(self) -> None:
        """``find/rfind`` would slice from the first opener to the last
        closer across the full string and fail to parse; the per-opener
        ``raw_decode`` scan picks the first valid object instead.
        """
        text = 'Use {x} as notation. Final JSON: {"k": "v"}'
        assert extract_json_from_llm_response(text) == {"k": "v"}


@pytest.mark.unit
class TestArrayExtractor:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("[1, 2, 3]", [1, 2, 3]),
            ("```json\n[1, 2]\n```", [1, 2]),
            ("Result: [1, 2] (done)", [1, 2]),
        ],
    )
    def test_happy_paths(self, text: str, expected: list[int]) -> None:
        assert extract_json_array_from_llm_response(text) == expected

    def test_object_input_rejected(self) -> None:
        """The array variant returns None when the response is a JSON object."""
        assert extract_json_array_from_llm_response('{"k": "v"}') is None

    def test_object_with_inner_array_does_not_extract_inner(self) -> None:
        """``{"items": [1, 2]}`` must not be unwrapped to ``[1, 2]``."""
        assert extract_json_array_from_llm_response('{"items": [1, 2]}') is None

    def test_empty_returns_none(self) -> None:
        assert extract_json_array_from_llm_response("") is None

    def test_stray_brackets_in_prose_do_not_defeat_fallback(self) -> None:
        """Prose with bracketed examples (``Example [a]``) before the
        actual array would defeat ``find/rfind``; the per-opener
        ``raw_decode`` scan finds the first valid array.
        """
        text = "Example [a] then result: [1, 2, 3]"
        assert extract_json_array_from_llm_response(text) == [1, 2, 3]
