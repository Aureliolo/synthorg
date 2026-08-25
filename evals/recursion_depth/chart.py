# module-kind: code
"""The deliverable: one chart, emitted as a self-contained SVG.

Hand-emitted rather than plotted by a library. The repository carries no
plotting dependency, a two-series six-point line chart with a cost panel does
not justify adding one, and a committed SVG diffs, renders in the docs and can
be read without running anything.

The palette is declared for both themes and the page paints its own background,
because an SVG with no background renders as light-on-light for half its
readers.

The caption is part of the chart rather than prose beside it. Three things must
travel with the curve wherever it is pasted: how many runs actually reached each
depth, that unit sizing was the planner's own, and what independence the judge
had. A number separated from its caveats gets over-read, which is the failure
this whole experiment exists downstream of.
"""

from collections.abc import Iterable
from typing import Final

from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import DepthPoint

#: Chart geometry, in user units. The viewBox scales, so these are ratios
#: rather than pixels.
_WIDTH: Final[int] = 900
_PLOT_HEIGHT: Final[int] = 380
_COST_HEIGHT: Final[int] = 170
_MARGIN_LEFT: Final[int] = 70
_MARGIN_RIGHT: Final[int] = 30
_MARGIN_TOP: Final[int] = 48
_GAP: Final[int] = 64
_CAPTION_LINE_HEIGHT: Final[int] = 18

#: Y-axis gridlines on the survival panel, as fractions.
_GRID_FRACTIONS: Final[tuple[float, ...]] = (0.0, 0.25, 0.5, 0.75, 1.0)

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


def _series(points: Iterable[DepthPoint]) -> dict[Arm, list[tuple[int, float]]]:
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


def _cost_series(points: Iterable[DepthPoint]) -> dict[Arm, list[tuple[int, float]]]:
    """Split points into one cost series per arm.

    Returns:
        Ascending ``(depth, cost)`` pairs per arm.
    """
    series: dict[Arm, list[tuple[int, float]]] = {arm: [] for arm in Arm}
    for point in points:
        # A bucket with no run in it has no spend to plot. It cannot arise from
        # a run whose leaves all failed, which is the case that used to need a
        # separate count: such a run still scores against the specification and
        # still books what it cost.
        if point.cells == 0:
            continue
        series[point.arm].append((point.depth, point.cost))
    for values in series.values():
        values.sort()
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


def _survival_panel(
    series: dict[Arm, list[tuple[int, float]]],
    xs: dict[int, float],
    secondary: dict[Arm, list[tuple[int, float]]],
) -> list[str]:
    """Draw the survival axes, gridlines and lines.

    The cap curve is drawn first and faint, so it sits behind the primary one:
    the two answer different questions and the achieved-depth curve is the
    finding. Drawn at all because a planner that stops splitting at three
    produces identical trees at caps four, five and six, and the pair read
    together is what distinguishes "gating holds at depth" from "nothing went
    there".

    Returns:
        The SVG fragments.
    """
    top = float(_MARGIN_TOP)
    parts: list[str] = [
        (
            f'<text class="title" x="{_MARGIN_LEFT}" y="26">'
            "Fraction of leaf work surviving to a correct merged result</text>"
        )
    ]
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
    for arm, values in secondary.items():
        if not values:
            continue
        colour, dash = _ARM_STYLE[arm]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        points = _polyline(values, xs, top=top, height=_PLOT_HEIGHT, ceiling=1.0)
        parts.append(
            f'<polyline class="series secondary" points="{points}" '
            f'stroke="{colour}"{dash_attr}/>'
        )
    for arm, values in series.items():
        if not values:
            continue
        colour, dash = _ARM_STYLE[arm]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        points = _polyline(values, xs, top=top, height=_PLOT_HEIGHT, ceiling=1.0)
        parts.append(
            f'<polyline class="series" points="{points}" stroke="{colour}"{dash_attr}/>'
        )
        parts.extend(
            f'<circle cx="{xs[depth]:.1f}" '
            f'cy="{top + _PLOT_HEIGHT - value * _PLOT_HEIGHT:.1f}" '
            f'r="{_POINT_RADIUS}" fill="{colour}"/>'
            for depth, value in values
        )
    return parts


def _cost_panel(
    series: dict[Arm, list[tuple[int, float]]], xs: dict[int, float]
) -> list[str]:
    """Draw the cost-against-depth panel beneath the survival curve.

    Present because the gated arm reviews every merge and both arms spend the
    same attempt budget: a reader has to be able to see what the gating bought
    rather than take it on the survival line alone.

    Returns:
        The SVG fragments.
    """
    top = float(_MARGIN_TOP + _PLOT_HEIGHT + _GAP)
    ceiling = max(
        (value for values in series.values() for _, value in values), default=0.0
    )
    parts: list[str] = [
        (
            f'<text class="title" x="{_MARGIN_LEFT}" y="{top - 16:.1f}">'
            "Spend per run, same axis</text>"
        ),
        (
            f'<line class="axis" x1="{_MARGIN_LEFT}" y1="{top + _COST_HEIGHT:.1f}" '
            f'x2="{_WIDTH - _MARGIN_RIGHT}" y2="{top + _COST_HEIGHT:.1f}"/>'
        ),
        (
            f'<text class="tick" x="{_MARGIN_LEFT - 10}" y="{top + 10:.1f}" '
            f'text-anchor="end">{ceiling:.2f}</text>'
        ),
        (
            f'<text class="tick" x="{_MARGIN_LEFT - 10}" '
            f'y="{top + _COST_HEIGHT + 4:.1f}" text-anchor="end">0</text>'
        ),
    ]
    for arm, values in series.items():
        if not values:
            continue
        colour, dash = _ARM_STYLE[arm]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        points = _polyline(
            values, xs, top=top, height=_COST_HEIGHT, ceiling=ceiling or 1.0
        )
        parts.append(
            f'<polyline class="series" points="{points}" stroke="{colour}"{dash_attr}/>'
        )
    return parts


def _legend(top: float) -> list[str]:
    """Draw the two-line legend.

    Returns:
        The SVG fragments.
    """
    parts: list[str] = []
    for index, arm in enumerate(Arm):
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
    faint_x = _MARGIN_LEFT + len(Arm) * 200
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
) -> str:
    """Render the survival curve, the cost panel and the caption as one SVG.

    Args:
        points: The primary curve, binned on the depth each leaf reached, one
            entry per ``(depth, arm)``.
        caption_lines: What must travel with the number wherever it is pasted.
        by_cap: The same measurement binned on the cap the run was allowed,
            drawn faint behind the primary curve. Empty draws nothing.

    Returns:
        A self-contained SVG document.
    """
    depths = tuple(sorted({point.depth for point in (*points, *by_cap)}))
    xs = _x_positions(depths) if depths else {}
    survival = _series(points)
    capped = _series(by_cap)
    costs = _cost_series(points)
    legend_top = float(_MARGIN_TOP + _PLOT_HEIGHT + _GAP + _COST_HEIGHT + 34)
    wrapped = [line for text in caption_lines for line in _wrap(text, 110)]
    caption_top = legend_top + 30
    height = int(caption_top + len(wrapped) * _CAPTION_LINE_HEIGHT + 20)
    body = [
        *_survival_panel(survival, xs, capped),
        (
            f'<text class="axis-label" x="{_WIDTH / 2:.0f}" '
            f'y="{_MARGIN_TOP + _PLOT_HEIGHT + 44}" text-anchor="middle">'
            "depth reached (levels of decomposition)</text>"
        ),
        *_cost_panel(costs, xs),
        *_legend(legend_top),
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
aria-label="Fraction of leaf work surviving to a correct merged result, by depth, \
gated and ungated">
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
  </style>
  <rect class="bg" x="0" y="0" width="{_WIDTH}" height="{height}"/>
  {drawn}
</svg>
"""


__all__ = ["render_chart"]
