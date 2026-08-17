# module-kind: code
"""Turning an activity row's references into the names an operator reads.

The feed stores references and nothing else, because a name written into the row
goes stale the moment an agent is renamed or a task retitled. The names are
resolved here, at the read boundary, exactly as ``api/controllers/cockpit.py``
already resolves them for its own rows.

Only the page being returned is enriched, not the whole window: a 7-day timeline
can run to thousands of events and every one of them would cost a lookup, while
a page costs one roster read plus one bounded task read.
"""

from collections.abc import Iterable, Sequence

from synthorg.api._read_names import agent_name_map, resolved_actor_name, task_titles
from synthorg.api.state import AppState
from synthorg.hr.activity import ActivityEvent

#: Where an event's subject task is referenced, most specific first. A delegation
#: names ``original_task_id`` and carries no ``task_id`` at all, so keying on one
#: name alone left every delegation row with nothing to show for its subject.
_TASK_ID_KEYS: tuple[str, ...] = ("task_id", "original_task_id")


def _task_reference(event: ActivityEvent) -> str | None:
    """The task this event is about, or ``None`` when it names none.

    Returns:
        The task reference, or ``None``.
    """
    for key in _TASK_ID_KEYS:
        reference = event.related_ids.get(key)
        if reference is not None:
            return reference
    return None


def _referenced_tasks(events: Iterable[ActivityEvent]) -> list[str]:
    """Every distinct task reference across the page.

    Returns:
        The references, deduplicated, in first-seen order.
    """
    references = [
        reference
        for reference in (_task_reference(event) for event in events)
        if reference is not None
    ]
    return list(dict.fromkeys(references))


async def enrich_activity_names(
    app_state: AppState,
    events: Sequence[ActivityEvent],
) -> tuple[ActivityEvent, ...]:
    """Fill each event's ``actor_name`` and ``subject_title``.

    Best-effort in one direction only: a reference nothing names is left as
    ``None`` so the surface says so in its own words, and never as the key it
    stands for.

    Returns:
        The same events, with the names their references resolve to.
    """
    if not events:
        return ()

    names = await agent_name_map(app_state)
    titles = await task_titles(app_state, _referenced_tasks(events))

    enriched: list[ActivityEvent] = []
    for event in events:
        actor = resolved_actor_name(event.related_ids.get("agent_id"), names)
        reference = _task_reference(event)
        title = None if reference is None else titles.get(reference)
        enriched.append(
            event.model_copy(update={"actor_name": actor, "subject_title": title})
        )
    return tuple(enriched)


__all__ = ["enrich_activity_names"]
