import { useLayoutEffect, type RefObject } from 'react'

/** Grow the composer to at most this many rows before it scrolls internally. */
const DEFAULT_MAX_ROWS = 8

/** Fallback line height (px) when the computed value is ``normal`` / unparsable. */
const FALLBACK_LINE_HEIGHT = 20

function numeric(value: string, fallback: number): number {
  const parsed = Number.parseFloat(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

/**
 * Auto-grow a textarea to fit its content, capped at ``maxRows`` (then it
 * scrolls internally). Runs on every value change so the composer expands as
 * the operator types a paragraph and shrinks back when they clear it. Drives
 * the height via inline style (which also overrides the field's manual-resize
 * handle), so the field never needs to be dragged.
 */
export function useTextareaAutogrow(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string,
  maxRows: number = DEFAULT_MAX_ROWS,
): void {
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.resize = 'none'
    // Reset first so scrollHeight reflects the content, not the prior height.
    el.style.height = 'auto'
    const style = window.getComputedStyle(el)
    const lineHeight = numeric(style.lineHeight, FALLBACK_LINE_HEIGHT)
    const paddingY =
      numeric(style.paddingTop, 0) + numeric(style.paddingBottom, 0)
    const borderY =
      numeric(style.borderTopWidth, 0) + numeric(style.borderBottomWidth, 0)
    const maxHeight = lineHeight * maxRows + paddingY + borderY
    const next = Math.min(el.scrollHeight, maxHeight)
    el.style.height = `${next}px`
    el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
  }, [ref, value, maxRows])
}
