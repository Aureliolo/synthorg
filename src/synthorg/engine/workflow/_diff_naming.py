# module-kind: code
"""The words a workflow diff row uses in place of a node or edge id.

A node's id is generated; its ``label`` is what its author typed and what the
editor renders, so a diff row that named the id would be describing a change to
something the operator has never seen. An edge is named by its own label where
it has one, and by the two nodes it joins where it does not.

``None`` is the honest answer where nothing supplies a label, and the surface
then says so in its own words. It is never the id.
"""

from collections.abc import Mapping
from typing import NamedTuple

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition, WorkflowEdge


class EdgeNaming(NamedTuple):
    """The words a diff row has for one edge, in place of its id."""

    edge: NotBlankStr | None
    source: NotBlankStr | None
    target: NotBlankStr | None


def as_label(value: str | None) -> NotBlankStr | None:
    """Narrow a looked-up label to the non-blank type the fields declare.

    Returns:
        The label, or ``None`` when there is none to show.
    """
    return NotBlankStr(value) if value else None


def node_labels(*definitions: WorkflowDefinition) -> dict[str, str]:
    """Every node label across *definitions*, later ones winning.

    An edge can outlive one end of itself within a diff, so both versions
    contribute: a removed node still labels the edge that used to reach it.

    Returns:
        Node id to display label.
    """
    return {
        node.id: node.label for definition in definitions for node in definition.nodes
    }


def edge_naming(edge: WorkflowEdge, labels: Mapping[str, str]) -> EdgeNaming:
    """How a row names *edge*: its own label, and the nodes it joins.

    Returns:
        The three labels, each ``None`` where nothing supplies one.
    """
    return EdgeNaming(
        edge=edge.label,
        source=as_label(labels.get(edge.source_node_id)),
        target=as_label(labels.get(edge.target_node_id)),
    )


__all__ = ["EdgeNaming", "as_label", "edge_naming", "node_labels"]
