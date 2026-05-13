"""Hybrid Plan + ReAct execution loop helpers, split by concern.

Direct imports only:
``from synthorg.engine.hybrid.step_helpers import build_step_message`` and
``from synthorg.engine.hybrid.replan_helpers import do_replan``. No
re-exports; this package's ``__init__`` deliberately stays empty so the
boundary between step-execution helpers and replanning helpers is
explicit at every call site.
"""
