"""Tests for the sandbox-category forwarding gate."""

import ast

import pytest
from scripts.check_sandbox_category_forwarded import _is_sandbox_execute, _violations

pytestmark = pytest.mark.unit


def _write(tmp_path, source: str):  # type: ignore[no-untyped-def]
    """Write *source* to a module in *tmp_path* and return its path."""
    path = tmp_path / "tool.py"
    path.write_text(source, encoding="utf-8")
    return path


class TestDetection:
    def test_a_call_without_a_category_is_flagged(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = _write(
            tmp_path,
            "async def run(self):\n"
            "    return await self._sandbox.execute(command='bash')\n",
        )
        assert len(_violations(path)) == 1

    def test_a_call_forwarding_its_category_passes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = _write(
            tmp_path,
            "async def run(self):\n"
            "    return await self._sandbox.execute(\n"
            "        command='bash', category=self.category.value\n"
            "    )\n",
        )
        assert _violations(path) == []

    @pytest.mark.parametrize(
        "value",
        [
            "''",
            "None",
            "ToolCategory.WEB.value",
            "self._category",
        ],
        ids=["empty", "none", "another_tools_category", "some_other_attribute"],
    )
    def test_a_borrowed_category_is_flagged(self, tmp_path, value) -> None:  # type: ignore[no-untyped-def]
        """Presence of the keyword was never the property worth checking.

        The argument decides the container runtime AND whether the workspace
        mount is writable, so a wrong one is worse than none: an empty string
        resolves to "no category" and silently takes the global default, and a
        borrowed one hands a read-only tool a writable mount. The gate's own
        message already prescribes the forwarding expression; this makes it
        check for it.
        """
        path = _write(
            tmp_path,
            "async def run(self):\n"
            "    return await self._sandbox.execute(\n"
            f"        command='bash', category={value}\n"
            "    )\n",
        )
        assert len(_violations(path)) == 1

    def test_the_marker_exempts_a_call(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = _write(
            tmp_path,
            "async def run(self):\n"
            "    return await self._sandbox.execute(  "
            "# lint-allow: sandbox-category -- probe\n"
            "        command='bash'\n"
            "    )\n",
        )
        assert _violations(path) == []

    def test_an_unrelated_execute_is_not_flagged(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The gate keys on the receiver, so a database cursor is left alone."""
        path = _write(
            tmp_path,
            "async def run(self):\n    return await self._cursor.execute('SELECT 1')\n",
        )
        assert _violations(path) == []


class TestReceiverMatching:
    @pytest.mark.parametrize("receiver", ["self._sandbox", "self.sandbox", "sandbox"])
    def test_every_sandbox_spelling_is_recognised(self, receiver: str) -> None:
        call = ast.parse(f"{receiver}.execute()").body[0].value  # type: ignore[attr-defined]
        assert _is_sandbox_execute(call)

    def test_a_different_method_on_a_sandbox_is_ignored(self) -> None:
        call = ast.parse("self._sandbox.health_check()").body[0].value  # type: ignore[attr-defined]
        assert not _is_sandbox_execute(call)
