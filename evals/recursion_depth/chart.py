# module-kind: code
"""The deliverable: one chart, emitted as a self-contained SVG.

Hand-emitted rather than plotted by a library. The repository carries no
plotting dependency, three stacked panels of a two-series six-point line do not
justify adding one, and a committed SVG diffs, renders in the docs and can be
read without running anything.

The palette is declared for both themes and the page paints its own background,
because an SVG with no background renders as light-on-light for half its
readers.

The caption is part of the chart rather than prose beside it. Five things must
travel with the curve wherever it is pasted: what each of the three panels
measures, which of them is the headline, how many runs actually reached each
depth, that unit sizing was the planner's own, and what independence the judge
had. A number separated from its caveats gets over-read, which is the failure
this whole experiment exists downstream of.
"""

from collections.abc import Iterable
from typing import Final

from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import (
    DepthPoint,
    SurvivalPoint,
    TokensPerSolvedPoint,
)

#: Chart geometry, in user units. The viewBox scales, so these are ratios
#: rather than pixels.
_WIDTH: Final[int] = 900
_PLOT_HEIGHT: Final[int] = 380
_EFFICIENCY_HEIGHT: Final[int] = 200

#: Half-width of an interval's end cap.
_WHISKER_CAP: Final[int] = 5
_MARGIN_LEFT: Final[int] = 70
_MARGIN_RIGHT: Final[int] = 30
_MARGIN_TOP: Final[int] = 48
_GAP: Final[int] = 64
_CAPTION_LINE_HEIGHT: Final[int] = 18

#: Y-axis gridlines on a fraction panel, as fractions.
_GRID_FRACTIONS: Final[tuple[float, ...]] = (0.0, 0.25, 0.5, 0.75, 1.0)

#: Where each panel's plot area starts, derived once so a panel and the thing
#: below it cannot disagree about how tall the one above was.
_SPEC_PANEL_TOP: Final[float] = float(_MARGIN_TOP)
_SURVIVAL_PANEL_TOP: Final[float] = _SPEC_PANEL_TOP + _PLOT_HEIGHT + _GAP
_EFFICIENCY_PANEL_TOP: Final[float] = _SURVIVAL_PANEL_TOP + _PLOT_HEIGHT + _GAP

#: Radius of a plotted point.
_POINT_RADIUS: Final[int] = 4

#: Per-arm colours, chosen to stay distinguishable in both themes and to
#: survive greyscale printing through their line style as well as their hue.
_ARM_STYLE: Final[dict[Arm, tuple[str, str]]] = {
    Arm.GATED: ("var(--gated)", ""),
    Arm.UNGATED: ("var(--ungated)", "6 4"),
}


def _escape(text: str) -> str:
    """Escape text for an SVG text node.

    Returns:
        The escaped text.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _wrap(text: str, width: int) -> list[str]:
    """Wrap caption prose to *width* characters.

    Returns:
        The wrapped lines.
    """
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        if current and sum(len(w) + 1 for w in current) + len(word) > width:
            lines.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _series(
    points: Iterable[DepthPoint | SurvivalPoint],
) -> dict[Arm, list[tuple[int, float]]]:
    """Split points into one plottable series per arm.

    A point with no delivered work carries no fraction and is left out rather
    than drawn at zero: nothing was measured there, and a zero would read as
    everything having been lost.

    Returns:
        Ascending ``(depth, fraction)`` pairs per arm.
    """
    series: dict[Arm, list[tuple[int, float]]] = {arm: [] for arm in Arm}
    for point in points:
        fraction = point.fraction
        if fraction is None:
            continue
        series[point.arm].append((point.depth, fraction))
    for values in series.values():
        values.sort()
    return series


def _efficiency_series(
    points: Iterable[TokensPerSolvedPoint],
) -> dict[Arm, list[TokensPerSolvedPoint]]:
    """Split points into one plottable series per arm.

    A bucket that solved nothing has no finite cost per solved requirement
    and is left out rather than drawn at the ceiling: nothing finite was
    measured there, and a point at the top would read as a measured maximum.

    Returns:
        Ascending points per arm, each carrying its own interval.
    """
    series: dict[Arm, list[TokensPerSolvedPoint]] = {arm: [] for arm in Arm}
    for point in points:
        if point.tokens_per_solved is None:
            continue
        series[point.arm].append(point)
    for values in series.values():
        values.sort(key=lambda point: point.depth)
    return series


def _x_positions(depths: tuple[int, ...]) -> dict[int, float]:
    """Map each plotted depth to an x coordinate.

    Returns:
        The x coordinate per depth.
    """
    span = _WIDTH - _MARGIN_LEFT - _MARGIN_RIGHT
    if len(depths) == 1:
        return {depths[0]: _MARGIN_LEFT + span / 2}
    step = span / (len(depths) - 1)
    return {depth: _MARGIN_LEFT + index * step for index, depth in enumerate(depths)}


def _polyline(
    values: list[tuple[int, float]],
    xs: dict[int, float],
    *,
    top: float,
    height: float,
    ceiling: float,
) -> str:
    """Render one series as an SVG points attribute.

    Returns:
        The space-separated coordinate pairs.
    """
    scale = height / ceiling if ceiling > 0 else 0.0
    return " ".join(
        f"{xs[depth]:.1f},{top + height - value * scale:.1f}" for depth, value in values
    )


def _fraction_panel(
    series: dict[Arm, list[tuple[int, float]]],
    xs: dict[int, float],
    *,
    top: float,
    title: str,
    secondary: dict[Arm, list[tuple[int, float]]] | None = None,
    note: str = "",
) -> list[str]:
    """Draw one fraction panel: axes, gridlines and lines.

    Both panels are the same drawing over a different ratio, and each names
    what it plots in its own title. The chart travels without the prose that
    qualifies it, so a panel whose title did not say which denominator it is
    over would be read as the other one.

    The cap curve, where a panel has one, is drawn first and faint so it sits
    behind the primary one: the two answer different questions and the
    achieved-depth curve is the finding. Drawn at all because a planner that
    stops splitting at three produces identical trees at caps four, five and
    six, and the pair read together is what distinguishes "gating holds at
    depth" from "nothing went there".

    Returns:
        The SVG fragments.
    """
    parts: list[str] = [
        (
            f'<text class="title" x="{_MARGIN_LEFT}" y="{top - 22:.1f}">'
            f"{_escape(title)}</text>"
        )
    ]
    if note:
        parts.append(
            f'<text class="caption" x="{_MARGIN_LEFT}" y="{top - 6:.1f}">'
            f"{_escape(note)}</text>"
        )
    for fraction in _GRID_FRACTIONS:
        y = top + _PLOT_HEIGHT - fraction * _PLOT_HEIGHT
        parts.append(
            f'<line class="grid" x1="{_MARGIN_LEFT}" y1="{y:.1f}" '
            f'x2="{_WIDTH - _MARGIN_RIGHT}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{_MARGIN_LEFT - 10}" y="{y + 4:.1f}" '
            f'text-anchor="end">{fraction:.2f}</text>'
        )
    for depth, x in xs.items():
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{top + _PLOT_HEIGHT + 20:.1f}" '
            f'text-anchor="middle">{depth}</text>'
        )
    parts.extend(_fraction_lines(secondary or {}, xs, top=top, primary=False))
    parts.extend(_fraction_lines(series, xs, top=top, primary=True))
    return parts


def _fraction_lines(
    series: dict[Arm, list[tuple[int, float]]],
    xs: dict[int, float],
    *,
    top: float,
    primary: bool,
) -> list[str]:
    """Draw one set of arm lines onto a fraction panel.

    Args:
        series: Each arm's points, keyed by arm.
        xs: Where each depth sits horizontally.
        top: The panel's top edge.
        primary: Whether these are the panel's own curve, which carries its
            point markers, or the faint one drawn behind it.

    Returns:
        The SVG fragments.
    """
    parts: list[str] = []
    for arm, values in series.items():
        if not values:
            continue
        colour, dash = _ARM_STYLE[arm]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        points = _polyline(values, xs, top=top, height=_PLOT_HEIGHT, ceiling=1.0)
        css = "series" if primary else "series secondary"
        parts.append(
            f'<polyline class="{css}" points="{points}" stroke="{colour}"{dash_attr}/>'
        )
        if not primary:
            continue
        parts.extend(
            f'<circle cx="{xs[depth]:.1f}" '
            f'cy="{top + _PLOT_HEIGHT - value * _PLOT_HEIGHT:.1f}" '
            f'r="{_POINT_RADIUS}" fill="{colour}"/>'
            for depth, value in values
        )
    return parts


def _efficiency_ceiling(series: dict[Arm, list[TokensPerSolvedPoint]]) -> float:
    """The largest figure the panel has to fit: a point or a bounded top.

    An open top is not a figure and sets no ceiling; its whisker runs to the
    panel's edge instead, which is what "no ceiling" looks like.

    Returns:
        The ceiling, ``0.0`` when nothing is plotted.
    """
    figures = [
        figure
        for points in series.values()
        for point in points
        for figure in (point.tokens_per_solved, point.ci_high)
        if figure is not None
    ]
    return max(figures, default=0.0)


def _whiskers(
    points: list[TokensPerSolvedPoint],
    xs: dict[int, float],
    *,
    top: float,
    ceiling: float,
    colour: str,
) -> list[str]:
    """Draw each point's interval as a vertical whisker with end caps.

    A point with no interval draws none, and a point whose interval is open
    above runs to the panel's top edge with no cap there: the cap is the
    claim that the interval ends, and for that point it does not.

    Returns:
        The SVG fragments.
    """
    scale = _EFFICIENCY_HEIGHT / ceiling if ceiling > 0 else 0.0
    baseline = top + _EFFICIENCY_HEIGHT
    parts: list[str] = []
    for point in points:
        if point.ci_low is None:
            continue
        x = xs[point.depth]
        y_low = baseline - point.ci_low * scale
        y_high = top if point.ci_high is None else baseline - point.ci_high * scale
        parts.append(
            f'<line class="whisker" x1="{x:.1f}" y1="{y_low:.1f}" '
            f'x2="{x:.1f}" y2="{y_high:.1f}" stroke="{colour}"/>'
        )
        parts.append(
            f'<line class="whisker" x1="{x - _WHISKER_CAP:.1f}" y1="{y_low:.1f}" '
            f'x2="{x + _WHISKER_CAP:.1f}" y2="{y_low:.1f}" stroke="{colour}"/>'
        )
        if point.ci_high is not None:
            parts.append(
                f'<line class="whisker" x1="{x - _WHISKER_CAP:.1f}" '
                f'y1="{y_high:.1f}" x2="{x + _WHISKER_CAP:.1f}" '
                f'y2="{y_high:.1f}" stroke="{colour}"/>'
            )
    return parts


def _efficiency_panel(
    series: dict[Arm, list[TokensPerSolvedPoint]], xs: dict[int, float]
) -> list[str]:
    """Draw the headline panel: tokens per solved requirement, with whiskers.

    Beneath the two fraction curves because it is read against them, and
    the headline because it is the axis that ranks the arms: the gated arm
    reviews every merge and both arms spend the same attempt budget, so what
    the gating bought is a cost per solved requirement, and whether the two
    arms' whiskers overlap is whether this recording can rank them.

    Returns:
        The SVG fragments.
    """
    top = _EFFICIENCY_PANEL_TOP
    ceiling = _efficiency_ceiling(series)
    parts: list[str] = [
        (
            f'<text class="title" x="{_MARGIN_LEFT}" y="{top - 16:.1f}">'
            "Tokens per solved requirement, 95% bootstrap interval (headline)"
            "</text>"
        ),
        (
            f'<line class="axis" x1="{_MARGIN_LEFT}" '
            f'y1="{top + _EFFICIENCY_HEIGHT:.1f}" '
            f'x2="{_WIDTH - _MARGIN_RIGHT}" y2="{top + _EFFICIENCY_HEIGHT:.1f}"/>'
        ),
        (
            f'<text class="tick" x="{_MARGIN_LEFT - 10}" y="{top + 10:.1f}" '
            f'text-anchor="end">{ceiling:,.0f}</text>'
        ),
        (
            f'<text class="tick" x="{_MARGIN_LEFT - 10}" '
            f'y="{top + _EFFICIENCY_HEIGHT + 4:.1f}" text-anchor="end">0</text>'
        ),
    ]
    for arm, points in series.items():
        if not points:
            continue
        colour, dash = _ARM_STYLE[arm]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        values = [
            (point.depth, point.tokens_per_solved)
            for point in points
            if point.tokens_per_solved is not None
        ]
        line = _polyline(
            values, xs, top=top, height=_EFFICIENCY_HEIGHT, ceiling=ceiling or 1.0
        )
        parts.extend(
            _whiskers(points, xs, top=top, ceiling=ceiling or 1.0, colour=colour)
        )
        parts.append(
            f'<polyline class="series" points="{line}" stroke="{colour}"{dash_attr}/>'
        )
    return parts


def _absent_note(absent: int) -> str:
    """Say how many survival buckets carry no point at all.

    A missing point on a line chart reads as a gap in the sweep rather than as
    a bucket whose delivered leaves claimed nothing, and the second is a fact
    about the plans rather than about coverage. Said on the panel because the
    chart is the thing that gets pasted somewhere else.

    Returns:
        The note, or the empty string when every bucket carries a rate.
    """
    if not absent:
        return ""
    return (
        f"{absent} bucket(s) have no point: their delivered leaves claimed "
        f"nothing, which is not a rate of zero."
    )


def _recorded_arms(
    *series: Iterable[DepthPoint | SurvivalPoint],
) -> tuple[Arm, ...]:
    """Which arms this recording actually holds points for.

    Read off every panel rather than the primary one alone, so an arm that
    reached only the survival axis still gets its key.

    Returns:
        The arms, in the enum's own order so the colours stay stable across
        recordings.
    """
    present = {point.arm for points in series for point in points}
    return tuple(arm for arm in Arm if arm in present)


def _legend(top: float, arms: tuple[Arm, ...]) -> list[str]:
    """Draw one entry per arm that RAN, plus the faint cap-curve entry.

    Derived from the recording rather than from ``Arm``'s members, because a
    matrix may declare one arm: a legend naming a line the chart does not draw
    reads as an arm that scored nothing rather than one that was not run.

    Args:
        top: Where the legend row sits.
        arms: The arms with points on the chart, in a stable order.

    Returns:
        The SVG fragments.
    """
    parts: list[str] = []
    for index, arm in enumerate(arms):
        colour, dash = _ARM_STYLE[arm]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        x = _MARGIN_LEFT + index * 200
        parts.append(
            f'<line class="series" x1="{x}" y1="{top:.1f}" x2="{x + 28}" '
            f'y2="{top:.1f}" stroke="{colour}"{dash_attr}/>'
        )
        label = "gate at every merge" if arm is Arm.GATED else "no merge gate"
        parts.append(
            f'<text class="legend" x="{x + 36}" y="{top + 4:.1f}">'
            f"{_escape(label)}</text>"
        )
    faint_x = _MARGIN_LEFT + len(arms) * 200
    parts.append(
        f'<line class="series secondary" x1="{faint_x}" y1="{top:.1f}" '
        f'x2="{faint_x + 28}" y2="{top:.1f}" stroke="var(--fg)"/>'
    )
    parts.append(
        f'<text class="legend" x="{faint_x + 36}" y="{top + 4:.1f}">'
        f"{_escape('faint: same runs binned on the depth cap allowed')}</text>"
    )
    return parts


def render_chart(
    *,
    points: tuple[DepthPoint, ...],
    caption_lines: tuple[str, ...],
    by_cap: tuple[DepthPoint, ...] = (),
    survival: tuple[SurvivalPoint, ...] = (),
    tokens_per_solved: tuple[TokensPerSolvedPoint, ...] = (),
) -> str:
    """Render the two fraction curves, the headline panel and the caption.

    The two fraction panels share an x axis and sit one above the other,
    because the pair coming apart is the finding: a specification line holding
    up while the survival line under it falls says the merging agent rebuilt
    the work, and that reading is unavailable from either panel alone. The
    headline panel sits beneath them on the same axis, because what a solved
    requirement cost is read against what was solved.

    Args:
        points: The specification curve, binned on the depth each leaf reached,
            one entry per ``(depth, arm)``.
        caption_lines: What must travel with the number wherever it is pasted.
        by_cap: The same measurement binned on the cap the run was allowed,
            drawn faint behind the primary curve. Empty draws nothing.
        survival: The leaf-work survival curve, on the same axis. Empty draws
            an empty panel rather than none, so a chart that measured no
            attributable work says so where the line would be.
        tokens_per_solved: The headline curve, each point carrying its own
            interval, drawn as whiskers. Empty draws an empty panel for the
            reason ``survival`` does.

    Returns:
        A self-contained SVG document.
    """
    plotted: set[int] = {point.depth for point in points}
    plotted.update(point.depth for point in by_cap)
    plotted.update(point.depth for point in survival)
    plotted.update(point.depth for point in tokens_per_solved)
    depths = tuple(sorted(plotted))
    xs = _x_positions(depths) if depths else {}
    efficiency = _efficiency_series(tokens_per_solved)
    absent = sum(1 for point in survival if point.fraction is None)
    legend_top = _EFFICIENCY_PANEL_TOP + _EFFICIENCY_HEIGHT + 34
    wrapped = [line for text in caption_lines for line in _wrap(text, 110)]
    caption_top = legend_top + 30
    height = int(caption_top + len(wrapped) * _CAPTION_LINE_HEIGHT + 20)
    body = [
        *_fraction_panel(
            _series(points),
            xs,
            top=_SPEC_PANEL_TOP,
            title="Fraction of the specification satisfied after the root merge",
            secondary=_series(by_cap),
        ),
        *_fraction_panel(
            _series(survival),
            xs,
            top=_SURVIVAL_PANEL_TOP,
            title="Fraction of the delivered leaves' own claims the merge kept",
            note=_absent_note(absent),
        ),
        (
            f'<text class="axis-label" x="{_WIDTH / 2:.0f}" '
            f'y="{_SURVIVAL_PANEL_TOP + _PLOT_HEIGHT + 44:.0f}" '
            'text-anchor="middle">'
            "depth reached (levels of decomposition)</text>"
        ),
        *_efficiency_panel(efficiency, xs),
        *_legend(legend_top, _recorded_arms(points, by_cap, survival)),
    ]
    body.extend(
        f'<text class="caption" x="{_MARGIN_LEFT}" '
        f'y="{caption_top + index * _CAPTION_LINE_HEIGHT:.0f}">{_escape(line)}</text>'
        for index, line in enumerate(wrapped)
    )
    return _document(height=height, body=body)


def _document(*, height: int, body: list[str]) -> str:
    """Wrap rendered fragments in a themed, self-contained SVG document.

    Returns:
        The SVG text.
    """
    drawn = "\n  ".join(body)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {height}" \
width="{_WIDTH}" height="{height}" role="img" \
aria-label="Fraction of the specification satisfied after the root merge, fraction \
of the delivered leaves' own claims the merge kept, and tokens per solved requirement \
with its bootstrap interval, by depth, gated and ungated">
  <style>
    :root {{
      --bg: #ffffff;
      --fg: #1a1a1a;
      --muted: #6b6b6b;
      --grid: #e2e2e2;
      --gated: #2f6f4f;
      --ungated: #a4432f;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #16181c;
        --fg: #e8e8e8;
        --muted: #a0a0a0;
        --grid: #2c2f36;
        --gated: #6fbf95;
        --ungated: #e08a72;
      }}
    }}
    .bg {{ fill: var(--bg); }}
    .title {{ fill: var(--fg); font: 600 15px system-ui, sans-serif; }}
    .axis-label {{ fill: var(--muted); font: 13px system-ui, sans-serif; }}
    .tick {{ fill: var(--muted); font: 12px system-ui, sans-serif; }}
    .legend {{ fill: var(--fg); font: 13px system-ui, sans-serif; }}
    .caption {{ fill: var(--muted); font: 12px system-ui, sans-serif; }}
    .grid {{ stroke: var(--grid); stroke-width: 1; }}
    .axis {{ stroke: var(--grid); stroke-width: 1.5; }}
    .series {{ fill: none; stroke-width: 2.5; stroke-linejoin: round; }}
    .secondary {{ stroke-width: 1.25; opacity: 0.4; }}
    .whisker {{ stroke-width: 1.5; opacity: 0.75; }}
  </style>
  <rect class="bg" x="0" y="0" width="{_WIDTH}" height="{height}"/>
  {drawn}
</svg>
"""


__all__ = ["render_chart"]
