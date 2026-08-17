#!/usr/bin/env python3
"""Gate: an operator surface shows names, never the keys they stand for.

An id is a database key. It is not memorable, not comparable by eye, and it
crowds out the name it stands in for; where it lands in prose it tells the
operator that a UUID is talking to them. So the backend resolves every
reference to a name at the read boundary and sends both, and the surface
renders the name or its own words for "nobody" -- never the key. The rule is
stated in ``src/synthorg/api/_read_names.py``: "A key is never returned,
because a surface handed one prints it."

Three shipped regressions, at three different boundaries, which is why the
gate asks the question at all three:

* ``web/src/pages/**`` rendered a key as a JSX text child, so a cockpit row was
  headed by an agent UUID and a plan card was owned by ``agent-7f3c...``.
* ``web/src/api/endpoints/activities.ts`` assigned ``related_ids.agent_id`` to
  the ``agentName`` field the feed renders in bold. The render site looked
  correct; the mapping one layer up was the leak, and a gate that only reads
  JSX cannot see it.
* ``src/synthorg/hr/activity.py`` interpolated the task id into the event's own
  prose (``f"Task {record.task_id} produced no artifacts"``), so the id
  survived even once the name beside it was resolved.

All three are shapes a reviewer reads past, and none is visible to a type
checker. So they are decided here instead.

What is flagged
---------------

**A rendered text child.** A JSX expression container in prose position whose
expression ends in a reference::

    <span>{row.taskId}</span>                         # flagged
    <td>Owner: {plan.owner}</td>                      # flagged
    <span>{agent.name}</span>                         # fine
    <li key={item.id} id={rowId}>                     # fine (React's, the DOM's)
    <Link to={`/tasks/${t.task_id}`}>{t.title}</Link>  # fine (routing)

A container is a text child when the character before it does not make it
something else: an attribute value, a template substitution, or the inner half
of a nested literal. Prose beside the expression does not exempt it.

**A name-shaped attribute.** ``aria-label`` and ``title`` are prose a screen
reader reads aloud; ``name`` is what a component renders. Interpolated or
bare, both leak::

    aria-label={`Delete backup ${backup.backup_id}`}   # flagged
    aria-label={row.taskId}                            # flagged
    <Avatar name={contribution.agent_id} />            # flagged

**A name-shaped field fed a key**, anywhere under ``web/src``::

    agentName: event.related_ids.agent_id             # flagged
    agentName: AgentId                                 # fine (a type, not a value)
    agentName: event.actor_name ?? UNKNOWN_AGENT_NAME  # fine

**Timeline prose built from a reference.** A ``*Event`` whose ``description``
is an f-string interpolating one::

    ActivityEvent(description=f"Task {record.task_id} started")  # flagged
    ActivityEvent(description="Task started")  # fine
    ActivityEvent(description=f"Task {status}")  # fine

The f-string is resolved one hop back through a local name, because
``desc = f"..."`` on one line and ``description=desc`` on the next is the idiom
this codebase actually writes; requiring it inline made the rule blind to the
module it was written to guard. ``str(record.task_id)`` and
``record["task_id"]`` are read too, from the parsed node rather than its source
text.

Only f-strings are considered: a plain ``description="..."`` on a Pydantic
``Field`` is schema prose, and carries no interpolation to leak.

What is deliberately NOT checked, so nobody mistakes silence for coverage:

* A value already destructured out of its object (``const { owner } = plan``
  and then ``{owner}``). A rendered container is decided on the member-access
  path, because a lone name inside braces is as likely to BE a destructure or
  an import specifier as to be a value, and telling those apart needs a parser.
  A template substitution, a name-shaped attribute and a property's right-hand
  side can only be values, so a lone name does count in those three positions.
* A ternary (``{t.owner ? t.owner : 'Unassigned'}``). The leading path there is
  a CONDITION, not the printed value; the printed values are in the branches.
* A call taking more than one argument. One argument is read through, because a
  formatter prints what it is handed; past that, which argument reaches the
  screen is a question about the callee.
* Whether the name rendered instead is the RIGHT one. That is a test.
* Tests, stories and mocks. A fixture is not a surface, and a story that
  deliberately shows the unresolved state is a legitimate thing to build.

The Python half judges every ``*Event(...)`` construction in ``src/synthorg``,
by suffix. That is broad on purpose rather than narrow: the suffix is what an
operator-facing timeline row is named by across the tree, and an open rule that
occasionally asks for a marker beats a hand-listed set that silently stops
covering a module somebody renames.

Opting out
----------

Per-line, with a mandatory reason. In JSX the marker goes on the line above,
because a comment placed inside the text it annotates becomes a child node of
that text; in Python it goes on the line above or the line itself::

    {/* lint-allow: id-in-ui -- <why this value is a word, not a key> */}
    // lint-allow: id-in-ui -- <reason>     (a .ts mapping site)
    # lint-allow: id-in-ui -- <reason>      (a Python event description)

The reason is mandatory because every legitimate case is a claim that the value
is something a person reads (a model id, an author-chosen slug, a support
reference), and this is the only place that claim gets written down. That makes
the reason checkable, which is the point: a marker asserting a workflow node id
was "chosen by its author" was refuted by reading the generator, which mints it
from a UUID. There is no baseline file: the rule ships with zero offenders.

Run from the repository root. Exits non-zero on any violation.
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

_MARKER: Final[re.Pattern[str]] = re.compile(r"\blint-allow:\s*id-in-ui\s*--\s*\S")
"""The per-line opt-out. The trailing ``\\S`` is the mandatory reason."""

#: Directories whose contents are fixtures rather than surfaces. Matched as
#: whole path segments, so a page under ``pages/demo-mocks/`` stays in scope.
_EXEMPT_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {"__tests__", "mocks", "test-infra"}
)

#: File-name fragments marking a fixture. A story that deliberately shows the
#: unresolved state is a legitimate thing to build.
_EXEMPT_FILE_MARKERS: Final[tuple[str, ...]] = (".test.", ".stories.")

#: A reference by the shape of its name. Open-ended on purpose: a field added
#: next year is refused until somebody writes down why it is a word, which is
#: the opposite default from a declared list.
_ID_SUFFIXES: Final[tuple[str, ...]] = ("_id", "Id")

_ID_EXEMPT_SUFFIXES: Final[tuple[str, ...]] = (
    "model_id",
    "modelId",
    "correlation_id",
    "correlationId",
)
"""References that ARE the word a person reads, tree-wide.

A model id is the string an operator types into the picker and the provider
bills against. A correlation id is a support reference whose whole purpose is
to be quoted back. Matched by suffix so ``recommended_model_id`` is covered
too; both would otherwise need the same marker on every one of their sites.
"""

_KEYED_REFERENCES: Final[frozenset[str]] = frozenset(
    {
        "assigned_to",
        "created_by",
        "decided_by",
        "executor",
        "lead",
        "owner",
        "requested_by",
        "reviewer",
    }
)
"""References whose names carry no suffix to recognise them by.

Every one is a key into something that HAS a human name, which is exactly the
set the backend resolves one for. Declared because the suffix rule cannot see
them, and it grows with that set.
"""

#: Fields whose own names promise a word, so feeding one a key is the leak.
_NAME_SHAPED_SUFFIXES: Final[tuple[str, ...]] = ("name", "title", "label")

#: An expression container holding no nested braces, so a nested object literal
#: or an arrow-function child is not matched: neither is a bare value being
#: printed, which is the only shape this decides.
_EXPRESSION_CONTAINER: Final[re.Pattern[str]] = re.compile(r"\{([^{}]+)\}")

#: What the character before a container means it is, when it is one of these:
#: an attribute value (``key={t.id}``), a template substitution (``${t.id}``),
#: the inner half of a nested literal (``style={{...}}``), or a statement block
#: opening after a call or a previous statement (``for (...) { ... }``).
_NOT_A_TEXT_CHILD: Final[frozenset[str]] = frozenset({"=", "$", "{", ")", ";"})

#: An arrow-function body is code, not prose: ``onClick={() => { f(x) }}``.
#: Two characters, so it cannot be decided by the single-character set above,
#: and ``>`` alone must stay allowed because it is what closes a JSX tag.
_ARROW_BODY: Final[str] = "=>"

#: A JSX attribute whose own name promises a word, so feeding it a reference is
#: the leak. ``aria-label`` and ``title`` are prose a screen reader reads aloud;
#: ``name`` is what a component like ``<Avatar name={...} />`` renders. Matched
#: whether the value is a template literal or a bare expression, because
#: ``aria-label={row.taskId}`` leaks exactly as much as the interpolated form.
_NAME_SHAPED_ATTRIBUTE: Final[re.Pattern[str]] = re.compile(
    r"(?P<attr>aria-label|title|name)=\{(?P<value>`[^`]*`|[^{}]+)\}",
)

_TEMPLATE_SUBSTITUTION: Final[re.Pattern[str]] = re.compile(r"\$\{([^{}]+)\}")

#: A property assignment in an object literal, e.g. ``agentName: e.agent_id,``.
#: The key must open the property: at the start of a line, or straight after a
#: ``{`` or a ``,``. Without that anchor the colon of a ternary reads as a
#: property separator, so ``isAgent ? data.name : node.id`` reports a ``name``
#: field fed ``node.id``, which is not a field at all.
#: The value must also look like a runtime expression: a lone PascalCase
#: identifier is a TypeScript type annotation (``agentName: AgentId``), which
#: renders nothing.
_OBJECT_PROPERTY: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[{,])\s*(?P<key>[A-Za-z_$][\w$]*)\s*:\s*(?P<value>[^,;\n]+)",
    re.MULTILINE,
)

#: A lone capitalised identifier: a type reference, not a value.
_TYPE_REFERENCE: Final[re.Pattern[str]] = re.compile(r"^\s*[A-Z][\w$]*\s*$")

#: A single-argument call, e.g. ``formatLabel(contribution.agent_id)``. The
#: argument is what gets printed, so the call is transparent for this rule.
_SINGLE_ARGUMENT_CALL: Final[re.Pattern[str]] = re.compile(
    r"^\s*[A-Za-z_$][\w$.]*\(\s*(?P<argument>[^(),]+?)\s*\)\s*$",
)

#: The member-access path an expression leads with, ignoring an optional
#: ``?? fallback`` or ``|| fallback`` tail. ``a.b.c`` yields ``c``.
_LEADING_PATH: Final[re.Pattern[str]] = re.compile(
    r"^\s*[A-Za-z_$][\w$]*(?:(?:\?)?\.[A-Za-z_$][\w$]*)+",
)

#: A bare identifier standing alone, e.g. ``{agentId}``.
_BARE_IDENTIFIER: Final[re.Pattern[str]] = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*$")

#: The keyword whose value becomes a timeline row's own prose.
_TEXT_FIELD: Final[str] = "description"

_EVENT_SUFFIX: Final[str] = "Event"
"""Which Python constructions are judged.

Deliberately narrow. An id in a validation message names the node the author
themselves named; an id in a loop-prevention diagnostic is written for whoever
reads the logs. Neither is a surface. What IS a surface is the timeline an
operator watches, whose rows are ``*Event`` models, and that is where the
regression this gate exists for happened.
"""

#: How far above a site its opt-out may sit. A JSX marker is a comment block
#: above the element it covers, and a reason worth writing rarely fits on one
#: line, so the window has to clear a wrapped comment plus the opening tag.
_MARKER_LOOKBACK: Final[int] = 5


def _is_reference(leaf: str) -> bool:
    """Whether *leaf* names a reference rather than a word.

    A bare ``id`` counts. The positions where an id is legitimately used are
    excluded structurally instead: an attribute value by :func:`_is_text_child`,
    a React ``key`` and a route parameter by the same rule. Exempting the name
    everywhere to protect those positions blinded all three checks to the
    commonest field name a leak wears.

    Returns:
        ``True`` when a surface handed this value would print a key.
    """
    if any(leaf.endswith(suffix) for suffix in _ID_EXEMPT_SUFFIXES):
        return False
    if leaf in _KEYED_REFERENCES:
        return True
    return leaf == "id" or any(leaf.endswith(suffix) for suffix in _ID_SUFFIXES)


def _path_leaf(expression: str) -> str | None:
    """Return the final segment of the member-access path *expression* leads with.

    Only a plain path counts. A call, an index, a template literal or a
    comparison is not a bare value being printed, and reading a name out of one
    would be guessing.

    Returns:
        The leaf name, or ``None`` when the expression is not a path.
    """
    match = _LEADING_PATH.match(expression)
    if match is None:
        return None
    tail = expression[match.end() :].strip()
    # A fallback is the one tail that leaves the head a printed value.
    if tail and not tail.startswith(("??", "||")):
        return None
    return match.group(0).replace("?.", ".").rsplit(".", maxsplit=1)[1]


def _value_leaf(expression: str) -> str | None:
    """As :func:`_path_leaf`, but a bare identifier and a call count too.

    Only for positions where a lone name can be nothing but a value: a template
    substitution, a name-shaped attribute, and the right-hand side of a
    property. In a JSX text container it could equally be a destructure or an
    import specifier, which is why the rendered-text check does not use this.

    A single-argument call is read through to its argument, because a formatter
    prints what it is handed: ``formatLabel(contribution.agent_id)`` renders the
    id just as plainly as the bare path does.

    Returns:
        The leaf name, or ``None``.
    """
    call = _SINGLE_ARGUMENT_CALL.match(expression)
    if call is not None:
        return _value_leaf(call.group("argument"))
    template = _template_leaf(expression)
    if template is not None:
        return template
    bare = _BARE_IDENTIFIER.match(expression)
    if bare is not None:
        return bare.group(1)
    return _path_leaf(expression)


def _template_leaf(expression: str) -> str | None:
    """Return the first reference a template literal would interpolate.

    Returns:
        The leaf name, or ``None`` when the expression is not a template or
        interpolates no reference.
    """
    if not expression.strip().startswith("`"):
        return None
    for substitution in _TEMPLATE_SUBSTITUTION.finditer(expression):
        leaf = _value_leaf(substitution.group(1))
        if leaf is not None and _is_reference(leaf):
            return leaf
    return None


@dataclass(frozen=True, slots=True)
class Violation:
    """One site that would put a key in front of an operator."""

    path: Path
    line: int
    expression: str

    def render(self) -> str:
        """One line for the failure report.

        The path is shown relative to the repository when it sits inside it,
        and as given otherwise: a report is no place to raise, and the caller
        decides which files to check.

        Returns:
            The formatted violation.
        """
        try:
            shown = self.path.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            shown = self.path.as_posix()
        return f"  {shown}:{self.line}: renders `{self.expression}`"


def _line_of(text: str, index: int) -> int:
    """Return the 1-based line number of *index* in *text*.

    Returns:
        The line number.
    """
    return text.count("\n", 0, index) + 1


def _marked(lines: list[str], line_number: int) -> bool:
    """Whether the site carries its opt-out.

    Returns:
        ``True`` when the site is opted out with a reason.
    """
    start = max(0, line_number - _MARKER_LOOKBACK)
    return any(_MARKER.search(line) for line in lines[start:line_number])


def _is_text_child(source: str, start: int) -> bool:
    """Whether the container at *start* is prose rather than something else.

    Read backwards past whitespace so a container on its own line is judged by
    the character that put it there, not by the newline.

    Returns:
        ``True`` when the container is a JSX text child.
    """
    before = source[:start].rstrip()
    if not before or before[-1] in _NOT_A_TEXT_CHILD:
        return False
    return not before.endswith(_ARROW_BODY)


def _text_child_violations(path: Path, source: str) -> Iterator[Violation]:
    """Every reference this component would render as prose.

    Yields:
        One violation per rendering site.
    """
    lines = source.splitlines()
    for match in _EXPRESSION_CONTAINER.finditer(source):
        if not _is_text_child(source, match.start()):
            continue
        expression = match.group(1)
        # A template literal and a single-argument call both print what they
        # wrap, so a text child is read through them; a lone identifier is not,
        # because in this position it is as likely to be a binding.
        leaf = _template_leaf(expression) or _call_or_path_leaf(expression)
        if leaf is None or not _is_reference(leaf):
            continue
        line = _line_of(source, match.start(1))
        if _marked(lines, line):
            continue
        yield Violation(path, line, expression.strip())


def _call_or_path_leaf(expression: str) -> str | None:
    """A member path, or the argument of a single-argument call around one.

    Returns:
        The leaf name, or ``None``.
    """
    call = _SINGLE_ARGUMENT_CALL.match(expression)
    if call is not None:
        return _value_leaf(call.group("argument"))
    return _path_leaf(expression)


def _label_violations(path: Path, source: str) -> Iterator[Violation]:
    """Every reference this component would read aloud or render as a name.

    Yields:
        One violation per accessible name.
    """
    lines = source.splitlines()
    for attribute in _NAME_SHAPED_ATTRIBUTE.finditer(source):
        value = attribute.group("value")
        leaf = _value_leaf(value)
        if leaf is None or not _is_reference(leaf):
            continue
        line = _line_of(source, attribute.start())
        if _marked(lines, line):
            continue
        yield Violation(path, line, f"{attribute.group('attr')}={{{value.strip()}}}")


def check_web_component(path: Path, source: str | None = None) -> list[Violation]:
    """Find references this component would show or read aloud.

    Returns:
        The violations, in source order.
    """
    text = path.read_text(encoding="utf-8") if source is None else source
    return [
        *_text_child_violations(path, text),
        *_label_violations(path, text),
    ]


def check_web_mapping(path: Path, source: str | None = None) -> list[Violation]:
    """Find name-shaped fields this module feeds a reference.

    Returns:
        The violations, in source order.
    """
    text = path.read_text(encoding="utf-8") if source is None else source
    lines = text.splitlines()
    violations: list[Violation] = []
    for match in _OBJECT_PROPERTY.finditer(text):
        key = match.group("key")
        if not any(key.lower().endswith(s) for s in _NAME_SHAPED_SUFFIXES):
            continue
        value = match.group("value")
        # ``agentName: AgentId`` is a type annotation, which renders nothing.
        # A line-oriented regex cannot tell a TS type position from a value
        # position, so the shape of the right-hand side has to decide it.
        if _TYPE_REFERENCE.match(value):
            continue
        leaf = _value_leaf(value)
        if leaf is None or not _is_reference(leaf):
            continue
        line = _line_of(text, match.start())
        if _marked(lines, line):
            continue
        violations.append(Violation(path, line, match.group(0).strip()))
    return violations


def _node_leaf(node: ast.expr) -> str | None:
    """The name an interpolated expression ends in, read from the AST.

    Walking the parsed node rather than splitting its source text is what makes
    ``str(record.task_id)`` and ``record["task_id"]`` visible: both print the
    reference, and neither is a bare dotted path.

    Returns:
        The leaf name, or ``None`` when the node names nothing.
    """
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
        return _node_leaf(node.value)
    if isinstance(node, ast.Call):
        # A formatter prints what it is handed, so read through a single
        # argument exactly as the web half does.
        if len(node.args) == 1 and not node.keywords:
            return _node_leaf(node.args[0])
        return None
    return None


def _interpolated_references(node: ast.JoinedStr, source: str) -> list[str]:
    """Every id-named name this f-string would interpolate.

    Returns:
        The offending expressions, as written.
    """
    found: list[str] = []
    for part in node.values:
        if not isinstance(part, ast.FormattedValue):
            continue
        leaf = _node_leaf(part.value)
        if leaf is not None and _is_reference(leaf):
            found.append(ast.get_source_segment(source, part.value) or leaf)
    return found


def _constructs_an_event(node: ast.Call) -> bool:
    """Whether this call builds a timeline row an operator reads.

    Returns:
        ``True`` for ``SomethingEvent(...)`` under any import spelling.
    """
    callee = node.func
    name = (
        callee.id
        if isinstance(callee, ast.Name)
        else callee.attr
        if isinstance(callee, ast.Attribute)
        else ""
    )
    return name.endswith(_EVENT_SUFFIX)


class UnparseableSourceError(Exception):
    """A file the gate was asked to judge and could not read.

    Raised rather than skipped: a gate that reports clean on a file it never
    parsed is the failure mode this whole module exists to refuse.
    """


def _bound_f_strings(tree: ast.Module) -> dict[str, ast.JoinedStr]:
    """Every module-or-function-local name assigned an f-string.

    ``description=f"..."`` written inline is the shape a reviewer notices. The
    shape that actually ships is a `desc = f"..."` one line above and
    ``description=desc`` below it, which is the idiom `hr/activity.py` uses
    throughout, so resolving one hop back is the difference between a rule and
    a decoration.

    Returns:
        Name to the f-string last assigned to it.
    """
    bound: dict[str, ast.JoinedStr] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(
            node.value, ast.JoinedStr
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bound[target.id] = node.value
    return bound


def _prose_f_string(
    value: ast.expr, bound: dict[str, ast.JoinedStr]
) -> ast.JoinedStr | None:
    """The f-string a ``description=`` keyword ultimately carries.

    Returns:
        The f-string node, or ``None`` when the value is not one.
    """
    if isinstance(value, ast.JoinedStr):
        return value
    if isinstance(value, ast.Name):
        return bound.get(value.id)
    return None


def check_python_file(path: Path, source: str | None = None) -> list[Violation]:
    """Find timeline prose this module builds out of a reference.

    Returns:
        The violations, in source order.

    Raises:
        UnparseableSourceError: when the file cannot be parsed, so the caller fails
            rather than recording a clean result it never earned.
    """
    text = path.read_text(encoding="utf-8") if source is None else source
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        message = f"{path.as_posix()}: {exc}"
        raise UnparseableSourceError(message) from exc
    bound = _bound_f_strings(tree)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _constructs_an_event(node):
            continue
        for keyword in node.keywords:
            if keyword.arg != _TEXT_FIELD:
                continue
            prose = _prose_f_string(keyword.value, bound)
            if prose is None:
                continue
            line = keyword.value.lineno
            if _marked(lines, line):
                continue
            violations.extend(
                Violation(path, line, written)
                for written in _interpolated_references(prose, text)
            )
    return violations


def _is_fixture(path: Path) -> bool:
    """Whether *path* is a fixture rather than a surface.

    Decided on whole path SEGMENTS and the file name, never a substring of the
    full path: a bare substring test exempts any surface that happens to sit
    under a directory whose name contains one of the markers.

    Returns:
        ``True`` when the file is out of scope.
    """
    if path.parts and any(part in _EXEMPT_DIRECTORIES for part in path.parts):
        return True
    return any(marker in path.name for marker in _EXEMPT_FILE_MARKERS)


#: The trees a whole-tree run covers. Checked for existence before scanning:
#: silence from a scan that found nothing TO scan is the failure mode every
#: whole-tree gate has to refuse.
_SCAN_ROOTS: Final[tuple[str, ...]] = ("web/src", "src/synthorg")


def _missing_roots(root: Path) -> list[str]:
    """Which scan roots are absent.

    Returns:
        The missing paths, as declared.
    """
    return [rel for rel in _SCAN_ROOTS if not (root / rel).is_dir()]


def _tree_files(root: Path) -> Iterator[Path]:
    """Every file the gate scans when given no explicit paths.

    Yields:
        Paths to check.
    """
    web_src = root / "web" / "src"
    # Every `.ts` under web/src, not just the API layer: a name-shaped field can
    # be filled in a store, a hook or a page helper just as easily as in an
    # endpoint module, and scanning one directory made the mapping boundary a
    # claim rather than a rule.
    for pattern in ("*.tsx", "*.ts"):
        for path in sorted(web_src.rglob(pattern)):
            if not _is_fixture(path):
                yield path
    yield from sorted((root / "src" / "synthorg").rglob("*.py"))


def _check(paths: Iterable[Path]) -> list[Violation]:
    """Run the right checker for each path.

    Returns:
        Every violation found.

    Raises:
        UnparseableSourceError: propagated from a Python file that will not parse.
    """
    violations: list[Violation] = []
    for path in paths:
        if _is_fixture(path):
            continue
        if path.suffix == ".tsx":
            # A `.tsx` renders AND maps: an object literal inside a component
            # fills a name-shaped field exactly as one in an endpoint does.
            violations.extend(check_web_component(path))
            violations.extend(check_web_mapping(path))
        elif path.suffix == ".ts":
            violations.extend(check_web_mapping(path))
        elif path.suffix == ".py":
            violations.extend(check_python_file(path))
    return violations


def _report(violations: list[Violation]) -> int:
    """Print the failure and explain the fix.

    Returns:
        The process exit code.
    """
    if not violations:
        return 0
    print(
        f"{len(violations)} site(s) would show an operator a key, not a name:",
        file=sys.stderr,
    )
    for violation in violations:
        print(violation.render(), file=sys.stderr)
    print(
        "\nResolve the name at the read boundary instead. The backend helpers"
        "\nlive in src/synthorg/api/_read_names.py (agent_name_map,"
        "\nresolved_actor_name, task_titles, project_names); they answer None"
        "\nwhen nothing names the reference, and the surface supplies its own"
        "\nwords for that (web/src/utils/agents.ts: UNKNOWN_AGENT_NAME,"
        "\nUNASSIGNED_LABEL, SYSTEM_ACTOR_NAME)."
        "\n\nWhere the value genuinely IS the word a person reads, say so:"
        "\n    {/* lint-allow: id-in-ui -- <reason> */}",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Gate on operator surfaces that render a raw identifier.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files to check. Defaults to the whole tree.",
    )
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    args = parser.parse_args(argv)

    try:
        if args.paths:
            return _report(_check(_existing(args.paths)))
        missing = _missing_roots(args.repo_root)
        if missing:
            print(
                f"{', '.join(missing)} is missing; the gate cannot verify anything.",
                file=sys.stderr,
            )
            return 1
        return _report(_check(_tree_files(args.repo_root)))
    except UnparseableSourceError as exc:
        print(
            f"{exc}\n\nThe gate cannot judge a file it cannot parse, and will not"
            "\nreport clean on one. Fix the syntax, or exclude the file"
            "\ndeliberately if it is not Python the gate should read.",
            file=sys.stderr,
        )
        return 1


def _existing(paths: Iterable[Path]) -> Iterator[Path]:
    """Yield the paths that exist, saying so when one does not.

    Pre-commit passes deleted paths on a staged removal, which is why a missing
    path is skipped rather than fatal. It is still reported, because silence is
    how a mistyped path becomes a check nobody ran.

    Yields:
        Each path that exists.
    """
    for path in paths:
        if path.exists():
            yield path
        else:
            print(f"{path.as_posix()}: not found, skipped", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
