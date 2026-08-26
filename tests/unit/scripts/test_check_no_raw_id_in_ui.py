"""The gate that keeps keys off operator surfaces.

The flagged shapes are the ones a live run put in front of an operator: a
cockpit row headed by an agent UUID, an audit table whose actor column was a
key, an activity feed whose mapping layer assigned the id to the name field,
a timeline row whose own prose carried a task UUID. The unflagged shapes are
the ways an id is legitimately used, and the gate is worth nothing if it
cannot tell them apart: a React ``key``, a route parameter, an option
``value``.
"""

from pathlib import Path

import pytest
from scripts.check_no_raw_id_in_ui import (
    check_python_file,
    check_web_component,
    check_web_mapping,
    main,
)

pytestmark = pytest.mark.unit

_SAMPLE = Path("web/src/pages/Sample.tsx")
_MAPPING = Path("web/src/api/endpoints/sample.ts")
_MODULE = Path("src/synthorg/hr/sample.py")


def _hits(source: str) -> list[str]:
    """Expressions the component check would flag.

    Returns:
        One entry per violation, in source order.
    """
    return [v.expression for v in check_web_component(_SAMPLE, source)]


def _mapping_hits(source: str) -> list[str]:
    """Expressions the mapping check would flag.

    Returns:
        One entry per violation, in source order.
    """
    return [v.expression for v in check_web_mapping(_MAPPING, source)]


def _python_hits(source: str) -> list[str]:
    """Expressions the timeline-prose check would flag.

    Returns:
        One entry per violation, in source order.
    """
    return [v.expression for v in check_python_file(_MODULE, source)]


class TestRenderedText:
    def test_a_bare_agent_id_as_a_text_child(self) -> None:
        assert _hits('<span className="truncate">{activity.agent_id}</span>') == [
            "activity.agent_id"
        ]

    def test_an_id_with_a_fallback_is_still_an_id(self) -> None:
        # The fallback is what makes it look safe: it prints the key whenever
        # the key is there, which is almost always.
        assert len(_hits("<td>{record.agent_id ?? 'system'}</td>")) == 1

    def test_an_optional_chain_reaches_the_same_field(self) -> None:
        assert len(_hits("<span>{approval.agent?.requested_by}</span>")) == 1

    def test_a_suffix_named_reference_is_caught_without_being_declared(self) -> None:
        # The open half of the rule: a field nobody listed still defaults to
        # refused, which is the opposite default from an allowlist.
        for field in ("backup_id", "node_id", "execution_id", "simulationId"):
            assert _hits(f"<span>{{row.{field}}}</span>"), field

    def test_a_declared_reference_carrying_no_suffix_is_caught(self) -> None:
        # The closed half: these have no suffix to recognise them by.
        for field in ("assigned_to", "owner", "lead", "reviewer", "created_by"):
            assert _hits(f"<span>{{item.{field}}}</span>"), field

    @pytest.mark.parametrize(
        "source",
        [
            "<span>Owner: {plan.owner}</span>",
            "<span>{task.assigned_to} (assignee)</span>",
            "<p>\n  Lead: {project.lead}\n</p>",
        ],
        ids=["prose_before", "prose_after", "own_line"],
    )
    def test_prose_beside_the_container_does_not_hide_it(self, source: str) -> None:
        # Anchoring to a tag delimiter read only containers sitting flush
        # against one, which is the minority of the shapes people write.
        assert len(_hits(source)) == 1

    def test_two_adjacent_containers_are_both_read(self) -> None:
        # They share the brace between them, so consuming the trailing
        # delimiter would report the first and walk past the second.
        assert _hits("<span>{task.task_id}{task.assigned_to}</span>") == [
            "task.task_id",
            "task.assigned_to",
        ]

    def test_the_line_number_points_at_the_render(self) -> None:
        source = "<div>\n  <p>fine</p>\n  <span>{task.assigned_to}</span>\n</div>"
        assert check_web_component(_SAMPLE, source)[0].line == 3


class TestLegitimateUses:
    def test_a_react_key_is_not_a_render(self) -> None:
        assert _hits("<Row key={task.task_id} task={task} />") == []

    def test_a_route_parameter_is_not_a_render(self) -> None:
        source = "<Link to={ROUTES.TASK.replace(':id', task.task_id)}>Open</Link>"
        assert _hits(source) == []

    def test_an_option_value_is_not_a_render(self) -> None:
        assert _hits("<option value={agent.agent_id}>{agent.name}</option>") == []

    def test_the_resolved_name_beside_it_is_the_point(self) -> None:
        assert _hits("<span>{task.assigned_to_name ?? 'Unassigned'}</span>") == []

    def test_a_model_reference_is_the_word_an_operator_reads(self) -> None:
        # A model id IS what an operator picks a model by, tree-wide, so it
        # needs no marker on any of its sites.
        for field in ("model_id", "recommended_model_id", "correlation_id"):
            assert _hits(f"<span>{{row.{field}}}</span>") == [], field

    def test_a_call_wrapping_a_reference_is_still_the_reference(self) -> None:
        # A one-argument formatter prints what it was handed, so wrapping the
        # key in one changes how the line reads and not what reaches the screen.
        assert _hits("<span>{formatTask(task.task_id)}</span>") == [
            "formatTask(task.task_id)"
        ]

    def test_a_template_substitution_is_not_a_text_child(self) -> None:
        assert _hits("const anchor = `row-${task.task_id}`") == []

    def test_a_nested_literal_is_not_a_text_child(self) -> None:
        assert _hits("<div style={{ gridArea: row.task_id }} />") == []

    def test_a_ternary_condition_is_not_the_printed_value(self) -> None:
        # The path there is a condition; what prints is in the branches, and
        # reading those needs to know which branch is which.
        assert _hits("<span>{t.owner ? t.owner : 'Unassigned'}</span>") == []

    def test_a_destructure_is_not_a_render(self) -> None:
        # A lone name in braces is as likely to be a binding as a value, so the
        # declaration keyword in front of it is what settles which.
        assert _hits("const { agentId } = useParams()") == []

    def test_an_import_specifier_is_not_a_render(self) -> None:
        assert _hits("import { useId } from 'react'") == []

    def test_a_destructured_parameter_is_not_a_render(self) -> None:
        # No keyword in front of this one: the open paren is what marks it.
        assert _hits("useMemo(({ nodeId }) => nodeId, [])") == []

    def test_a_guarded_object_shorthand_is_not_a_render(self) -> None:
        # ``...(x !== undefined && { taskId })`` builds props conditionally; the
        # logical operator in front of the brace is what marks it as a literal.
        assert _hits("const p = { ...(taskId !== undefined && { taskId }) }") == []

    def test_a_lone_name_rendered_as_a_child_is_flagged(self) -> None:
        # The hole this closes: the drawer read `ID: {nodeId}`, whose value the
        # editor mints from a UUID, and every check above it looks past a name
        # standing on its own.
        assert _hits("<div>ID: {nodeId}</div>") == ["nodeId"]

    def test_a_lone_resolved_name_rendered_as_a_child_is_fine(self) -> None:
        assert _hits("<div>{agentName}</div>") == []


class TestComments:
    """Prose about code renders nothing, so a brace inside it leaks nothing."""

    def test_a_route_documented_in_a_docstring_is_not_a_render(self) -> None:
        # ``PATCH /agents/{id}`` in a JSDoc block documents a route. Read as
        # JSX it reported a leak on a file that renders no such thing.
        doc = "/**\n * Runs ``PATCH /agents/{id}``.\n */\nexport const x = 1"
        assert _hits(doc) == []

    def test_a_line_comment_is_not_a_render(self) -> None:
        assert _hits("// falls back to {task_id} when nothing names it\n") == []

    def test_a_url_in_a_string_does_not_swallow_the_line(self) -> None:
        # The ``//`` inside the literal is not a comment opener, so the render
        # after it on the same line is still read.
        assert _hits("<a href='https://x'>{row.task_id}</a>") == ["row.task_id"]

    def test_a_commented_out_render_is_not_a_render(self) -> None:
        assert _hits("{/* was <span>{row.task_id}</span> */}") == []

    def test_an_apostrophe_in_prose_does_not_stop_the_blanking(self) -> None:
        # Read as a string opener, the apostrophe in ``Owner's`` leaves the scan
        # inside a literal for the rest of the file, so the comment below it is
        # judged as code and its documented brace reported as a render. The gate
        # then fails a file that renders nothing of the kind.
        source = "<span>Owner's plan</span>\n/** PATCH /agents/{id}. */\n"
        assert _hits(source) == []

    def test_an_apostrophe_in_prose_still_leaves_a_later_render_visible(self) -> None:
        # The complement: treating the apostrophe as prose must not blind the
        # scan to what follows it either.
        source = "<span>Owner's plan</span>\n<span>{row.task_id}</span>\n"
        assert _hits(source) == ["row.task_id"]


class TestAccessibleNames:
    def test_an_identifier_read_aloud_is_flagged(self) -> None:
        # A screen reader is the only way to read an icon button's label. The
        # finding names the whole attribute, because the attribute is what the
        # reader has to go and change.
        source = "<Button aria-label={`Delete backup ${backup.backup_id}`} />"
        assert _hits(source) == ["aria-label={`Delete backup ${backup.backup_id}`}"]

    def test_a_bare_name_in_a_substitution_counts(self) -> None:
        # Unlike a JSX container, a substitution can only hold a value.
        assert _hits("<i title={`Task ${taskId}`} />") == ["title={`Task ${taskId}`}"]

    def test_a_bare_expression_leaks_as_much_as_an_interpolated_one(self) -> None:
        # `aria-label={row.taskId}` has no template literal to notice, which is
        # the shape that put a UUID into two labels a screen reader reads.
        assert _hits("<Button aria-label={row.taskId} />") == [
            "aria-label={row.taskId}"
        ]

    def test_a_resolved_name_read_aloud_is_fine(self) -> None:
        assert _hits("<Button aria-label={`Restore ${backup.takenAt}`} />") == []


class TestNameShapedFields:
    def test_a_key_assigned_to_a_name_field_is_flagged(self) -> None:
        # The shipped regression: the render site looked correct, and the
        # mapping one layer up was the leak.
        assert _mapping_hits("agentName: event.related_ids.agent_id,") == [
            "agentName: event.related_ids.agent_id"
        ]

    def test_a_bare_key_assigned_to_a_name_field_is_flagged(self) -> None:
        assert len(_mapping_hits("actorName: agentId,")) == 1

    def test_a_title_field_is_judged_the_same_way(self) -> None:
        assert len(_mapping_hits("subjectTitle: row.task_id,")) == 1

    def test_the_resolved_field_is_the_point(self) -> None:
        source = "agentName: event.actor_name ?? UNKNOWN_AGENT_NAME,"
        assert _mapping_hits(source) == []

    def test_a_field_that_promises_no_word_is_not_judged(self) -> None:
        assert _mapping_hits("agentId: event.related_ids.agent_id,") == []


class TestTimelineProse:
    def test_an_identifier_baked_into_a_row(self) -> None:
        source = (
            "ActivityEvent(\n"
            '    description=f"Task {record.task_id} produced no artifacts",\n'
            ")\n"
        )
        assert _python_hits(source) == ["record.task_id"]

    def test_it_is_caught_under_a_module_alias(self) -> None:
        source = 'activity.ActivityEvent(description=f"Task {task_id} started")\n'
        assert len(_python_hits(source)) == 1

    def test_prose_carrying_no_reference_passes(self) -> None:
        assert _python_hits('ActivityEvent(description=f"Task {status}")\n') == []

    def test_a_plain_string_passes(self) -> None:
        assert _python_hits('ActivityEvent(description="Task started")\n') == []

    def test_a_validation_message_is_not_a_surface(self) -> None:
        # The node id is the author's own label for it.
        source = 'ValidationIssue(message=f"node {node_id} is unreachable")\n'
        assert _python_hits(source) == []

    def test_a_schema_field_description_is_not_a_surface(self) -> None:
        assert _python_hits('Field(description=f"the {thing_id} to use")\n') == []

    def test_a_local_name_is_read_in_its_own_function(self) -> None:
        # ``desc`` is the obvious name for this, so two functions in one module
        # each hold one. Resolved module-wide, the clean second binding wins and
        # the leaking first site is reported nowhere.
        source = (
            "def leaks(record):\n"
            '    desc = f"Task {record.task_id} started"\n'
            "    return ActivityEvent(description=desc)\n"
            "\n\n"
            "def clean(record):\n"
            '    desc = f"Task {record.status}"\n'
            "    return ActivityEvent(description=desc)\n"
        )
        assert _python_hits(source) == ["record.task_id"]

    def test_a_module_level_binding_still_reaches_a_function(self) -> None:
        # Scoping resolves outward, so a constant built above the functions is
        # a real binding for every one of them.
        source = (
            'DESC = f"Task {task_id} started"\n'
            "\n\n"
            "def emit():\n"
            "    return ActivityEvent(description=DESC)\n"
        )
        assert _python_hits(source) == ["task_id"]


class TestSuppression:
    def test_a_justified_marker_silences_the_line(self) -> None:
        source = "<span>{task.task_id}</span> {/* lint-allow: id-in-ui -- x */}"
        assert _hits(source) == []

    def test_a_marker_without_a_reason_does_not(self) -> None:
        source = "<span>{task.task_id}</span> {/* lint-allow: id-in-ui */}"
        assert len(_hits(source)) == 1

    def test_a_marker_above_the_render_silences_it(self) -> None:
        # Where a JSX marker has to go: inside the element it would become a
        # child node of the text it annotates.
        source = "{/* lint-allow: id-in-ui -- x */}\n<span>{t.task_id}</span>"
        assert _hits(source) == []

    def test_a_wrapped_marker_still_reaches_its_site(self) -> None:
        source = (
            "{/* lint-allow: id-in-ui -- a reason long enough that it wraps\n"
            "    onto a second line, as a real one does. */}\n"
            "<span>{t.task_id}</span>"
        )
        assert _hits(source) == []

    def test_a_marker_far_above_does_not(self) -> None:
        marker = "{/* lint-allow: id-in-ui -- x */}"
        filler = "\n".join(["<br />"] * 6)
        assert len(_hits(f"{marker}\n{filler}\n<span>{{t.task_id}}</span>")) == 1


class TestCli:
    def test_the_tree_is_clean(self) -> None:
        """The gate ships passing, which is what makes a new hit a regression."""
        assert main([]) == 0

    def test_a_planted_violation_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "Component.tsx"
        path.write_text("<span>{row.taskId}</span>\n", encoding="utf-8")
        assert main([str(path)]) == 1

    def test_a_path_that_is_gone_is_ignored(self, tmp_path: Path) -> None:
        """Pre-commit passes deleted paths on a staged removal."""
        assert main([str(tmp_path / "missing.tsx")]) == 0

    def test_a_fixture_is_out_of_scope(self, tmp_path: Path) -> None:
        path = tmp_path / "Card.stories.tsx"
        path.write_text("<span>{row.taskId}</span>\n", encoding="utf-8")
        assert main([str(path)]) == 0

    def test_a_missing_tree_fails_rather_than_passing(self, tmp_path: Path) -> None:
        # Silence from a scan that found nothing to scan is the failure mode
        # every whole-tree gate has to refuse.
        assert main(["--repo-root", str(tmp_path)]) == 1
