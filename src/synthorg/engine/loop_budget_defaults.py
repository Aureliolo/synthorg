# module-kind: declarative
"""Fallback budgets bounding one agent run, used when settings do not resolve.

Each is the code default behind an operator setting, and each answers a
different question about a run that will not finish: how many turns it may
take, how many further budgets it may grant itself, and how long it may go
on producing turns that ran nothing at all. They live together because the
three only make sense against each other, and beside the loop modules that
read them rather than on the context object that happens to carry two.
"""

from typing import Final

DEFAULT_MAX_TURNS: Final[int] = 300
"""Default hard limit on LLM turns per agent execution.

A backstop against a pathological loop, not a work budget. What actually
bounds an ordinary run is its cost ceiling, its stagnation detector and its
stage timeout, each of which stops a run that is spending without
progressing. The turn cap only has to sit above what real work takes.

Twenty is a chat-assistant number: a build agent spends that reading the
code before it edits anything, so it ran out mid-build with real files
written and the run was discarded.

Fallback when ``engine.max_turns`` is not resolvable; the operator-tunable
value flows through that setting (see ``AgentEngine._resolve_max_turns``)."""

DEFAULT_MAX_TURN_EXTENSIONS: Final[int] = 3
"""How many further turn budgets a run may grant itself before it parks.

Reaching the cap usually means the work was bigger than the estimate, not
that anything is wrong, so the common case is answered by carrying on rather
than by interrupting a human. Bounded, because a run that has taken four
full budgets is no longer a long task, it is a question; at that point the
run parks with its workspace intact and asks whether to continue.

Zero restores the old behaviour: the first ceiling ends the run."""

DEFAULT_MAX_UNRESOLVED_TOOL_TURNS: Final[int] = 5
"""Consecutive turns that may resolve to no tool at all before a run stops.

A turn asking only for tools nobody has registered ran nothing, so it made no
progress by construction, whatever its arguments were. Small on purpose: the
registry answers the first such call by name with its nearest matches, so a
run that has not taken them by the fifth attempt is not going to. A live run
spent 246 turns asking for a tool named ``write``.

Fallback when ``engine.max_unresolved_tool_turns`` is not resolvable; zero
disables the stop."""
