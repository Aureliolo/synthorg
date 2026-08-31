"""Unit tests for ``scripts/check_posture_write_agrees_with_default.py``.

The gate exists because ``("engine", "reasoning_effort_low",
"none")`` shipped in the posture seeder: a write that happened to equal the
setting's registered default at the time it was written, which pins the
row against a later default change. The pure helpers (``_flatten_dispatch``,
``_check_writes``) are exercised directly against fixture data; ``_scan``
and ``main`` are exercised against the real installed package, since this
gate has no notion of a ``--repo-root`` to sandbox against.

Tests load the script via :mod:`importlib`, matching the pattern in
``test_check_capability_field_has_reader.py``.
"""

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_posture_write_agrees_with_default.py"


class _WriteView(Protocol):
    flag: str
    namespace: str
    key: str
    value: str


class _HitView(Protocol):
    write: _WriteView
    reason: str

    def message(self) -> str: ...


class _ScriptModule(Protocol):
    PostureConfigError: type[Exception]

    @staticmethod
    def _flatten_dispatch(dispatch: object) -> list[_WriteView]: ...
    @staticmethod
    def _check_writes(
        writes: list[_WriteView],
        get_default: Callable[[str, str], str | None],
    ) -> list[_HitView]: ...
    @staticmethod
    def _scan() -> list[_HitView]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_posture_write_agrees_with_default",
            _SCRIPT_PATH,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast(_ScriptModule, module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()


def _defaults(mapping: dict[tuple[str, str], str]) -> Callable[[str, str], str | None]:
    def _get_default(namespace: str, key: str) -> str | None:
        return mapping.get((namespace, key))

    return _get_default


class TestFlattenDispatch:
    def test_flattens_every_write_across_every_flag(self) -> None:
        dispatch = (
            ("flag_a", (("ns", "key1", "true"),)),
            ("flag_b", (("ns", "key2", "false"), ("ns", "key3", "medium"))),
        )
        writes = _MODULE._flatten_dispatch(dispatch)
        assert [(w.flag, w.namespace, w.key, w.value) for w in writes] == [
            ("flag_a", "ns", "key1", "true"),
            ("flag_b", "ns", "key2", "false"),
            ("flag_b", "ns", "key3", "medium"),
        ]

    def test_non_tuple_dispatch_raises(self) -> None:
        with pytest.raises(_MODULE.PostureConfigError):
            _MODULE._flatten_dispatch(["not", "a", "tuple"])

    def test_entry_not_a_pair_raises(self) -> None:
        with pytest.raises(_MODULE.PostureConfigError):
            _MODULE._flatten_dispatch((("flag_a", (("ns", "k", "v"),), "extra"),))

    def test_flag_not_a_string_raises(self) -> None:
        with pytest.raises(_MODULE.PostureConfigError):
            _MODULE._flatten_dispatch(((1, (("ns", "k", "v"),)),))

    def test_writes_not_a_tuple_raises(self) -> None:
        with pytest.raises(_MODULE.PostureConfigError):
            _MODULE._flatten_dispatch((("flag_a", [("ns", "k", "v")]),))

    def test_triple_not_three_strings_raises(self) -> None:
        with pytest.raises(_MODULE.PostureConfigError):
            _MODULE._flatten_dispatch((("flag_a", (("ns", "k"),)),))

    def test_triple_with_non_string_member_raises(self) -> None:
        with pytest.raises(_MODULE.PostureConfigError):
            _MODULE._flatten_dispatch((("flag_a", (("ns", "k", 1),)),))

    def test_empty_dispatch_flattens_to_no_writes(self) -> None:
        assert _MODULE._flatten_dispatch(()) == []


class TestCheckWrites:
    def test_write_matching_an_unregistered_key_is_a_hit(self) -> None:
        writes = _MODULE._flatten_dispatch((("flag_a", (("ns", "missing", "true"),)),))
        hits = _MODULE._check_writes(writes, _defaults({}))
        assert len(hits) == 1
        assert hits[0].reason == "no such setting is registered"

    def test_write_equal_to_the_default_is_a_hit(self) -> None:
        writes = _MODULE._flatten_dispatch((("flag_a", (("ns", "key", "true"),)),))
        hits = _MODULE._check_writes(writes, _defaults({("ns", "key"): "true"}))
        assert len(hits) == 1
        assert "equals the registered default" in hits[0].reason

    def test_write_differing_from_the_default_is_not_a_hit(self) -> None:
        writes = _MODULE._flatten_dispatch((("flag_a", (("ns", "key", "false"),)),))
        hits = _MODULE._check_writes(writes, _defaults({("ns", "key"): "true"}))
        assert hits == []

    def test_a_clean_bundle_alongside_a_bad_one_reports_only_the_bad_one(self) -> None:
        writes = _MODULE._flatten_dispatch(
            (
                ("flag_a", (("ns", "good", "false"),)),
                ("flag_b", (("ns", "bad", "true"),)),
            )
        )
        hits = _MODULE._check_writes(
            writes, _defaults({("ns", "good"): "true", ("ns", "bad"): "true"})
        )
        assert [h.write.key for h in hits] == ["bad"]


class TestHitMessage:
    def test_message_names_the_flag_the_key_and_the_reason(self) -> None:
        writes = _MODULE._flatten_dispatch((("my_flag", (("ns", "key", "true"),)),))
        hits = _MODULE._check_writes(writes, _defaults({("ns", "key"): "true"}))
        message = hits[0].message()
        assert "my_flag" in message
        assert "ns.key" in message
        assert "true" in message


class TestScanAndMain:
    def test_scan_reports_nothing_against_the_live_tree(self) -> None:
        """Regression guard: the shipped bundle must stay clean.

        This runs against the real, installed ``_posture_seeding`` module and
        the real settings registry, not a fixture, because the gate has no
        notion of a sandboxed tree.
        """
        assert _MODULE._scan() == []

    def test_main_exits_zero_against_the_live_tree(self) -> None:
        assert _MODULE.main([]) == 0

    def test_scan_raises_when_the_dispatch_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_MODULE, "_live_dispatch", lambda: ())
        with pytest.raises(_MODULE.PostureConfigError):
            _MODULE._scan()

    def test_main_exits_two_when_the_module_cannot_be_understood(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _broken() -> object:
            msg = "boom"
            raise _MODULE.PostureConfigError(msg)

        monkeypatch.setattr(_MODULE, "_live_dispatch", _broken)
        assert _MODULE.main([]) == 2

    def test_main_exits_one_when_the_live_dispatch_carries_a_no_op_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            _MODULE,
            "_live_dispatch",
            lambda: (("fake_flag", (("ns", "key", "true"),)),),
        )
        monkeypatch.setattr(_MODULE, "_live_default", lambda _ns, _key: "true")
        assert _MODULE.main([]) == 1
