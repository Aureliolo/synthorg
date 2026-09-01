"""Narrow a scoped MCP surface to the tools a unit of work is about.

Scoping decides what an agent MAY reach and is a security question with one
owner (``MCPToolScoper.visible_tools``). This is a different question asked
afterwards, of the survivors only: given a couple of hundred admissible
tools, which ones belong in front of the model for THIS task. Handing a
model every admissible tool is the shape under which selection accuracy
collapses as the surface grows, and the fix is retrieval, never a wider
grant, so this module can only ever drop from what scoping admitted and
cannot add to it.

Lexical on purpose. A tool is named and described in the same vocabulary a
task brief uses (``synthorg_tasks_list``, "List tasks with optional
filters"), the surface is a few hundred short documents, and the query is
one brief, so token overlap weighted by rarity across the surface answers
well enough that an embedding model, a network call and a second
``(provider, model)`` binding would buy nothing but latency and a knob.
"""

import math
import re
from collections.abc import Sequence
from typing import Final

from synthorg.core.task import Task
from synthorg.meta.mcp.registry import MCPToolDef
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

_TOKEN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")
_MIN_TOKEN_CHARS: Final[int] = 3
#: A name token is a stronger signal than a description token: the name IS
#: the tool's identity, while a description mentions neighbours.
_NAME_WEIGHT: Final[float] = 2.0


def tokenize(text: str) -> frozenset[str]:
    """Reduce *text* to the terms retrieval matches on.

    Lower-cased alphanumeric runs, dropping the very short ones that carry no
    meaning, with a trailing plural ``s`` stripped so a brief that says
    "tasks" meets a tool that says "task".

    Returns:
        The distinct terms.
    """
    terms: set[str] = set()
    for raw in _TOKEN.findall(text.lower()):
        if len(raw) < _MIN_TOKEN_CHARS:
            continue
        term = raw
        if term.endswith("s") and len(term) > _MIN_TOKEN_CHARS:
            term = term[:-1]
        terms.add(term)
    return frozenset(terms)


def rank_tools(
    tools: Sequence[MCPToolDef],
    *,
    query: str,
    top_k: int,
) -> tuple[MCPToolDef, ...]:
    """Keep the *top_k* tools most about *query*, in their original order.

    Every term is weighted by how rare it is across *tools*, so a term every
    tool carries (``synthorg``) decides nothing while a term one domain
    carries decides a lot. Ties keep the scoper's order, so the result is
    deterministic for a given surface and query.

    Args:
        tools: The scoped surface, as the scoper returned it.
        query: The text of the unit of work the surface is for.
        top_k: How many tools to keep; ``0`` keeps every one.

    Returns:
        The kept tools, in the order *tools* listed them.
    """
    if top_k <= 0 or len(tools) <= top_k:
        return tuple(tools)
    query_terms = tokenize(query)
    if not query_terms:
        return tuple(tools)

    name_terms = [tokenize(tool.name) for tool in tools]
    description_terms = [tokenize(tool.description) for tool in tools]
    document_frequency: dict[str, int] = {}
    for names, descriptions in zip(name_terms, description_terms, strict=True):
        for term in names | descriptions:
            document_frequency[term] = document_frequency.get(term, 0) + 1
    total = len(tools)

    def _score(index: int) -> float:
        score = 0.0
        for term in query_terms:
            frequency = document_frequency.get(term)
            if frequency is None:
                continue
            rarity = math.log(1.0 + total / frequency)
            if term in name_terms[index]:
                score += rarity * _NAME_WEIGHT
            elif term in description_terms[index]:
                score += rarity
        return score

    ranked = sorted(range(total), key=lambda index: (-_score(index), index))
    kept = sorted(ranked[:top_k])
    return tuple(tools[index] for index in kept)


def task_brief_text(task: Task) -> str:
    """The text of a task retrieval ranks the MCP surface against.

    Returns:
        The title and description, which is what the agent is briefed on.
    """
    return f"{task.title}\n{task.description}"


def latest_human_turn(conversation: Sequence[ChatMessage]) -> str | None:
    """The most recent human instruction in a taskless conversation.

    A chat action has no task; what it is about is whatever the human said
    last, which is also what the model is about to act on.

    Returns:
        That message's content, or ``None`` when nobody has spoken yet.
    """
    for message in reversed(conversation):
        if message.role is MessageRole.USER and message.content:
            return message.content
    return None


__all__ = ["latest_human_turn", "rank_tools", "task_brief_text", "tokenize"]
