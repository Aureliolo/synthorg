/** Shared recharts configuration. */

/**
 * First-paint size for `ResponsiveContainer` before its ResizeObserver
 * delivers real measurements.
 *
 * The library default is `{width: -1, height: -1}`, which fails its own
 * `width > 0 && height > 0` sanity check and logs a "width(-1) and
 * height(-1) of chart should be greater than 0" warning on every first
 * render. A degenerate placeholder like 1x1 silences that warning but
 * introduces a worse one: with a 1px-tall plot, chart margins and axis
 * space push the inner plot rect negative and recharts emits
 * `<rect height="NaN">` in each Area's clipPath ("Received NaN for the
 * height attribute"). The placeholder must therefore be a realistic
 * positive size; recharts swaps to the measured size on the next
 * animation frame, so the exact value never affects layout.
 */
export const CHART_INITIAL_DIMENSION = { width: 600, height: 320 } as const

/**
 * Numeric mirrors of the stroke-width design tokens for recharts props.
 *
 * recharts does arithmetic on `strokeWidth` (e.g. the reveal-animation
 * clipPath computes `parseInt(strokeWidth)` for its rect height), so a
 * CSS `var(--so-stroke-*)` string becomes NaN and React warns
 * "Received NaN for the height attribute" on every chart mount
 * animation. Keep these in sync with `--so-stroke-hairline` /
 * `--so-stroke-thin` in `design-tokens.css`; direct SVG/CSS consumers
 * keep using the CSS variables.
 */
export const CHART_STROKE_HAIRLINE = 1
export const CHART_STROKE_THIN = 1.5
