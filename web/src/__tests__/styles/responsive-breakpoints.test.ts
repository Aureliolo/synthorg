import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Tablet-breakpoint verification for the responsive width tokens: tablet
 * breakpoints tested at 768, 1024, 1280.
 *
 * jsdom does not compute CSS layout, so we cannot measure a rendered Drawer
 * directly. Instead we lock the contract in two complementary ways:
 *
 *  1. Parse `design-tokens.css` and pin the `clamp()` expressions themselves
 *     so a future edit to the lower/upper bounds is caught. The widths are
 *     density-aware, so swapping a bound is a visual regression.
 *  2. Simulate the clamp math at each breakpoint with a 1rem = 16px base and
 *     assert the resolved widths sit inside the intended visual band. That
 *     band is the functional contract: "Drawer fits inside the tablet viewport
 *     with breathing room on both sides; SearchInput does not overflow the
 *     content column on narrow tablets."
 */

const __dirname = dirname(fileURLToPath(import.meta.url))
const TOKENS_PATH = resolve(__dirname, '../../styles/design-tokens.css')
const TOKENS_CSS = readFileSync(TOKENS_PATH, 'utf8')

const REM_TO_PX = 16

function rem(value: number): number {
  return value * REM_TO_PX
}

/** Minimal `clamp(min, preferred, max)` evaluator for vw-based expressions. */
function clampVw(
  min: number,
  preferredVw: number,
  max: number,
  viewportPx: number,
): number {
  const preferred = (preferredVw / 100) * viewportPx
  return Math.min(Math.max(preferred, min), max)
}

function getTokenExpression(name: string): string {
  // Simple literal line scan -- the CSS file is ~300 lines and all tokens
  // are on their own line. Avoids feeding the interpolated token name to
  // RegExp (eslint-plugin-security: detect-non-literal-regexp).
  const needle = `${name}:`
  const lines = TOKENS_CSS.split('\n')
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed.startsWith(needle)) continue
    const after = trimmed.slice(needle.length)
    const semi = after.indexOf(';')
    return (semi === -1 ? after : after.slice(0, semi)).trim()
  }
  throw new Error(`Token ${name} not found in design-tokens.css`)
}

describe('responsive width tokens (tablet: 768 / 1024 / 1280)', () => {
  describe('design-tokens.css pins the clamp expressions', () => {
    it('defines drawer width bounds', () => {
      expect(getTokenExpression('--so-drawer-width-narrow')).toBe(
        'clamp(20rem, 40vw, 28rem)',
      )
      expect(getTokenExpression('--so-drawer-width-default')).toBe(
        'clamp(20rem, 45vw, 36rem)',
      )
      expect(getTokenExpression('--so-drawer-width-wide')).toBe(
        'clamp(24rem, 55vw, 48rem)',
      )
    })

    it('defines search-input width caps', () => {
      expect(getTokenExpression('--so-search-max-narrow')).toBe('20rem')
      expect(getTokenExpression('--so-search-max-wide')).toBe('32rem')
    })

    it('defines popover width caps', () => {
      expect(getTokenExpression('--so-popover-max-compact')).toBe('24rem')
      expect(getTokenExpression('--so-popover-max-wide')).toBe('48rem')
    })
  })

  describe.each([
    { viewport: 768, label: 'tablet-portrait (768px)' },
    { viewport: 1024, label: 'tablet-landscape (1024px)' },
    { viewport: 1280, label: 'desktop-small (1280px)' },
  ])('drawer widths at $label', ({ viewport }) => {
    it('default drawer leaves at least 200px of main-content breathing room', () => {
      const width = clampVw(rem(20), 45, rem(36), viewport)
      expect(width).toBeGreaterThanOrEqual(rem(20))
      expect(width).toBeLessThanOrEqual(rem(36))
      expect(viewport - width).toBeGreaterThanOrEqual(200)
    })

    it('wide drawer never exceeds the viewport or its 48rem cap', () => {
      const width = clampVw(rem(24), 55, rem(48), viewport)
      expect(width).toBeGreaterThanOrEqual(rem(24))
      expect(width).toBeLessThanOrEqual(rem(48))
      expect(width).toBeLessThanOrEqual(viewport)
    })

    it('narrow drawer fits comfortably on tablet', () => {
      const width = clampVw(rem(20), 40, rem(28), viewport)
      expect(width).toBeGreaterThanOrEqual(rem(20))
      expect(width).toBeLessThanOrEqual(rem(28))
    })
  })

  describe.each([
    { viewport: 768 },
    { viewport: 1024 },
    { viewport: 1280 },
  ])('search-input caps at $viewport px', ({ viewport }) => {
    const narrowCap = rem(20)
    const wideCap = rem(32)

    it('narrow cap never exceeds the viewport', () => {
      expect(narrowCap).toBeLessThanOrEqual(viewport)
    })

    it('wide cap never exceeds the viewport', () => {
      expect(wideCap).toBeLessThanOrEqual(viewport)
    })
  })

  describe.each([
    { viewport: 768 },
    { viewport: 1024 },
    { viewport: 1280 },
  ])('popover caps at $viewport px', ({ viewport }) => {
    const compactCap = rem(24)
    const wideCap = rem(48)

    it('compact popover fits within the viewport with margin', () => {
      expect(compactCap).toBeLessThanOrEqual(viewport - 48)
    })

    it('wide popover fits within the viewport at the 1024px tablet breakpoint and above', () => {
      if (viewport >= 1024) {
        expect(wideCap).toBeLessThanOrEqual(viewport - 32)
      } else {
        // At 768px the 48rem (768px) cap would fill the entire viewport; the
        // consumer is responsible for picking `compact` at narrow viewports.
        expect(wideCap).toBeGreaterThan(viewport - 32)
      }
    })
  })
})
