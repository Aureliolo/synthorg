import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { screen, waitFor, within } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ThemeStep } from '@/pages/setup/ThemeStep'
import { renderWithRouter } from '@/__tests__/test-utils'
import {
  ANIMATION_PRESETS,
  COLOR_PALETTES,
  DENSITIES,
  SIDEBAR_MODES,
} from '@/stores/theme'

// Comments are stripped so prose naming a selector (the density block is
// introduced by one) cannot be mistaken for a declaration block.
const TOKENS_CSS = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), '../../../styles/design-tokens.css'),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '')

const REM_TO_PX = 16

/** Every declaration a selector makes, across all of its blocks.
 *
 * Later declarations overwrite earlier ones, mirroring the cascade for a
 * selector that sets the same token more than once.
 */
function declarationsFor(scope: string): Map<string, string> {
  const declarations = new Map<string, string>()
  let seen = false
  let inScope = false
  for (const line of TOKENS_CSS.split('\n')) {
    const trimmed = line.trim()
    const colon = trimmed.indexOf(':')
    if (trimmed.endsWith('{')) {
      inScope = trimmed.slice(0, -1).trim() === scope
      seen ||= inScope
    } else if (trimmed === '}') {
      inScope = false
    } else if (inScope && colon > 0) {
      declarations.set(
        trimmed.slice(0, colon).trim(),
        trimmed.slice(colon + 1).replace(';', '').trim(),
      )
    }
  }
  if (!seen) throw new Error(`selector ${scope} not found in design tokens`)
  return declarations
}

/** Resolve a token to its pixel number, following `var()` hops.
 *
 * The option descriptions quote these pixel values as prose, which has no
 * compile-time link to the tokens they describe. Resolving the token here
 * means a retuned value fails the assertions below instead of silently
 * leaving the wizard describing a mode it no longer has.
 */
function tokenPx(name: string, scope = ':root'): number {
  const raw = declarationsFor(scope).get(name)
  if (raw === undefined) throw new Error(`token ${name} not set by ${scope}`)
  const hop = /^var\((--[\w-]+)\)$/.exec(raw)
  if (hop?.[1]) return tokenPx(hop[1])
  if (raw.endsWith('rem')) return Number.parseFloat(raw) * REM_TO_PX
  return Number.parseFloat(raw)
}

/** The description text of one radio option, located by its label. */
function descriptionOf(label: string): string {
  return screen.getByText(label).parentElement?.textContent ?? ''
}

/** Values of every radio in one option group, in render order.
 *
 * Each group is a `<fieldset>` with a `<legend>`, so it carries an implicit
 * `group` role named by that legend.
 */
function radioValues(groupLabel: string): string[] {
  return within(screen.getByRole('group', { name: groupLabel }))
    .getAllByRole<HTMLInputElement>('radio')
    .map((input) => input.value)
}

describe('ThemeStep', () => {
  it('offers every mode the store and backend accept', async () => {
    renderWithRouter(<ThemeStep />)
    await waitFor(() => expect(screen.getByText('Sidebar')).toBeInTheDocument())

    // Options are derived from the store's exported tuples, so a mode absent
    // from an option list is a type error rather than a silently missing
    // wizard choice; this asserts the rendered result matches.
    expect(radioValues('Sidebar')).toEqual([...SIDEBAR_MODES])
    expect(radioValues('Animation')).toEqual([...ANIMATION_PRESETS])
    expect(radioValues('Color Palette')).toEqual([...COLOR_PALETTES])
    expect(radioValues('Density')).toEqual([...DENSITIES])
  })

  it('quotes each sidebar width from its own token', async () => {
    renderWithRouter(<ThemeStep />)
    await waitFor(() => expect(screen.getByText('Rail')).toBeInTheDocument())

    const rail = tokenPx('--so-sidebar-collapsed')
    const compact = tokenPx('--so-sidebar-compact')
    const expanded = tokenPx('--so-sidebar-expanded')
    // If two widths ever collapse onto one number the `not.toContain`
    // assertions below stop discriminating, so pin distinctness first.
    expect(new Set([rail, compact, expanded]).size).toBe(3)

    // Rail is the icon-only mode and persistent the widest; each option must
    // quote its own width and no sibling's.
    expect(descriptionOf('Rail')).toContain(`${rail}px`)
    expect(descriptionOf('Rail')).not.toContain(`${expanded}px`)
    expect(descriptionOf('Persistent')).toContain(`${expanded}px`)
    expect(descriptionOf('Compact')).toContain(`${compact}px`)
    expect(descriptionOf('Compact')).not.toContain(`${rail}px`)
  })

  it('quotes each density padding from its own token', async () => {
    renderWithRouter(<ThemeStep />)
    await waitFor(() => expect(screen.getByText('Balanced')).toBeInTheDocument())

    // Balanced is the `:root` default; the rest are override classes.
    const paddings: readonly (readonly [string, number])[] = [
      ['Dense', tokenPx('--so-density-card-padding', '.density-dense')],
      ['Balanced', tokenPx('--so-density-card-padding')],
      ['Medium', tokenPx('--so-density-card-padding', '.density-medium')],
      ['Sparse', tokenPx('--so-density-card-padding', '.density-sparse')],
    ]
    for (const [label, padding] of paddings) {
      expect(descriptionOf(label)).toContain(`${padding}px`)
    }
  })
})
