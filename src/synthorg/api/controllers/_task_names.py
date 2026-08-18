# module-kind: code
"""The two name reads every task response makes, taken once and together.

A task row names an assignee and titles the dependencies it waits on. Neither
read needs the other's answer, and every task surface makes both, so they
overlap rather than stacking: the detail read for one task's dependencies, the
list read for every dependency on the page.

Shared so the two paths cannot drift. `dependency_titles` states that a
dependency absent from the map is one nothing could name, and a surface that
resolved none would be making that claim about every dependency it carried.
"""

import asyncio
from collections.abc import Iterable

from synthorg.api._read_names import agent_name_map, task_titles
from synthorg.api.state import AppState


async def names_and_titles(
    app_state: AppState,
    dependencies: Iterable[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve the agent names and the dependency titles one response needs.

    Args:
        app_state: Application state.
        dependencies: Every dependency referenced by the response, in any order
            and with repeats; the title read takes it as a set and chunks it, so
            a page costs one query per hundred distinct references rather than
            one per reference.

    Returns:
        The agent-name map and the dependency-title map.
    """
    async with asyncio.TaskGroup() as group:
        name_read = group.create_task(agent_name_map(app_state))
        title_read = group.create_task(task_titles(app_state, dependencies))
    return name_read.result(), title_read.result()
