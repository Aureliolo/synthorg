"""Shared test helpers usable from any tests/* subtree.

These helpers exist outside ``tests/unit/`` and ``tests/integration/``
so the same utility (``FakeClock``, ...) can be imported from any
test file regardless of marker. The package contains no test cases of
its own; pytest does not collect from this directory.
"""
