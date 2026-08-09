import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { Density } from '@/stores/theme'
import { cardPaddingFor } from '@/pages/org/layout-shared'

/**
 * The layout reserves a department card's inner padding before React renders
 * it, so its table has to track `--so-density-card-padding` rather than merely
 * having matched it once. Nothing else compares the two: a density retuned in
 * CSS alone would go on reserving the old value, and the first agent row would
 * crowd the stats bar at exactly the densities that were changed.
 */

const TOKENS_CSS = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), '../../../styles/design-tokens.css'),
  'utf8',
)

/** Selector carrying each density's card padding; balanced is the base. */
const SELECTOR_BY_DENSITY: Record<Density, string> = {
  dense: '.density-dense',
  medium: '.density-medium',
  balanced: ':root',
  sparse: '.density-sparse',
}

const PADDING_TOKEN = '--so-density-card-padding'

/** The stylesheet with comments removed, so prose braces cannot open a block. */
const DECLARATIONS = TOKENS_CSS.replace(/\/\*[\s\S]*?\*\//g, '')

/** The px value of every `--so-space-*` step. */
const SPACE_SCALE = new Map<string, number>(
  [...DECLARATIONS.matchAll(/(--so-space-[\d-]+):\s*(\d+)px;/g)].map(
    ([, name, px]) => [name!, Number(px)],
  ),
)

/** Every rule in the sheet, as its selector and its declaration body. */
function rules(): { selector: string; body: string }[] {
  return DECLARATIONS.split('}')
    .filter((chunk) => chunk.includes('{'))
    .map((chunk) => ({
      selector: chunk.slice(0, chunk.indexOf('{')).trim(),
      body: chunk.slice(chunk.indexOf('{') + 1),
    }))
}

/**
 * The px value `--so-density-card-padding` resolves to under one selector.
 *
 * `:root` carries several rules in this sheet, so the block is found by the
 * token it declares rather than by being the first of its selector.
 */
function cardPaddingToken(selector: string): number {
  for (const rule of rules()) {
    if (rule.selector !== selector) continue
    const declared = /--so-density-card-padding:\s*var\((--so-space-[\d-]+)\)/.exec(rule.body)
    if (!declared) continue
    const px = SPACE_SCALE.get(declared[1]!)
    if (px === undefined) throw new Error(`${selector} references unknown ${declared[1]!}`)
    return px
  }
  throw new Error(`${selector} does not declare ${PADDING_TOKEN}`)
}

describe('card padding tracks the density tokens', () => {
  it('resolves a padding token for every density', () => {
    const resolved = Object.values(SELECTOR_BY_DENSITY).map(cardPaddingToken)
    expect(resolved).toHaveLength(4)
    expect(new Set(resolved).size).toBe(4)
  })

  it.each(Object.keys(SELECTOR_BY_DENSITY) as Density[])(
    'reserves the %s card padding the stylesheet renders',
    (density) => {
      expect(cardPaddingFor(density)).toBe(cardPaddingToken(SELECTOR_BY_DENSITY[density]))
    },
  )

  it('falls back to the balanced padding when no density is supplied', () => {
    expect(cardPaddingFor(undefined)).toBe(cardPaddingToken(SELECTOR_BY_DENSITY.balanced))
  })

  it('keeps the ladder dense < medium < balanced < sparse', () => {
    const ladder: Density[] = ['dense', 'medium', 'balanced', 'sparse']
    const values = ladder.map((density) => cardPaddingFor(density))
    expect(values).toEqual([...values].sort((a, b) => a - b))
  })
})
