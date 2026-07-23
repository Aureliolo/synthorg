"""Scored A/B harness for the four inner execution loops.

Drives the same workspace-graded coding brief through every loop registered in
``synthorg.engine.loop_selector._LOOP_REGISTRY``, records what each run actually
cost in tokens and wall-clock, scores them on a common rubric, and emits a
commit-stamped scoreboard plus a promotion recommendation for the existing
``engine.default_loop_type`` / ``engine.loop_complexity_overrides`` settings.

The harness adds no loop-selection machinery and modifies no loop: every metric
the rubric consumes is already recorded on ``TurnRecord`` / ``ExecutionResult``
or in the gateway's ``CostRecord`` ledger.
"""
