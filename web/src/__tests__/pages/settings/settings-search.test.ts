import { describe, expect, it } from 'vitest'

import { makeSettingEntry as makeEntry } from '@/__tests__/helpers/factories'
import {
  filterByNamespace,
  filterNamespaceEntries,
} from '@/pages/settings/settings-page-helpers'
import { matchesSetting, scoreSetting } from '@/pages/settings/utils'

/**
 * Settings search matched but did not rank, and a subsequence match over a
 * sentence accepts nearly every query. Searching "decomposition model"
 * reported 88 results in fixed namespace order: the first screen was Auth
 * Revalidate Window Seconds, Ws Frame Timeout Seconds, Bulk Delete Budget
 * Seconds, and the setting actually NAMED Decomposition Model was far below.
 * An operator who knows a setting's name had no way to search for it.
 */

/** The setting the reported query was looking for. */
const DECOMPOSITION_MODEL = makeEntry({
  namespace: 'coordination',
  key: 'decomposition_model',
  group: 'Decomposition',
  description: 'The (provider, model) pair the planner runs on.',
})

/** One of the rows that outranked it: prose containing the query words. */
const AUTH_REVALIDATE = makeEntry({
  namespace: 'api',
  key: 'auth_revalidate_window_seconds',
  group: 'Auth',
  description:
    'How long a decomposition of the session model may go before the ' +
    'window is revalidated.',
})

describe('scoreSetting', () => {
  it('ranks a setting searched by its own name above prose that mentions it', () => {
    expect(scoreSetting(DECOMPOSITION_MODEL, 'decomposition model')).toBeGreaterThan(
      scoreSetting(AUTH_REVALIDATE, 'decomposition model'),
    )
  })

  it('ranks an exact name above a name that merely contains the query', () => {
    const longer = makeEntry({ key: 'decomposition_model_fallback' })

    expect(scoreSetting(DECOMPOSITION_MODEL, 'decomposition model')).toBeGreaterThan(
      scoreSetting(longer, 'decomposition model'),
    )
  })

  it('ranks a name prefix above a name that contains the query elsewhere', () => {
    const prefix = makeEntry({ key: 'decomposition_model_pins' })
    const buried = makeEntry({ key: 'fallback_decomposition_model_pins' })

    expect(scoreSetting(prefix, 'decomposition model')).toBeGreaterThan(
      scoreSetting(buried, 'decomposition model'),
    )
  })

  it('ranks a namespace or group match above a description match', () => {
    const byGroup = makeEntry({ group: 'Decomposition', key: 'unrelated_key' })
    const byDescription = makeEntry({
      key: 'unrelated_key',
      description: 'Decomposition happens here.',
    })

    expect(scoreSetting(byGroup, 'decomposition')).toBeGreaterThan(
      scoreSetting(byDescription, 'decomposition'),
    )
  })

  it('still finds a setting from an abbreviation, which is what fuzzy is for', () => {
    // "prt" matching server_port is why subsequence matching is kept at all:
    // the box has to work before a whole word has been typed.
    expect(scoreSetting(makeEntry({ key: 'server_port' }), 'prt')).toBeGreaterThan(0)
  })

  it('never lets a long query carry a weaker tier onto a stronger one', () => {
    // The per-term bonus is what makes a partial name match rank by how much
    // of the query it answers. Uncapped it is also unbounded, and past twenty
    // terms it closes the gap between tiers: a setting matching MOST of a long
    // query would outrank one matching ALL of it, which inverts the whole
    // ordering the tiers exist to express.
    const query = Array.from({ length: 40 }, (_, i) => `term${i}`).join(' ')
    const everyTerm = makeEntry({ key: query.split(' ').join('_') })
    const allButOne = makeEntry({
      key: query.split(' ').slice(0, -1).join('_'),
    })

    expect(scoreSetting(everyTerm, query)).toBeGreaterThan(
      scoreSetting(allButOne, query),
    )
  })

  it('scores nothing for a query the setting does not answer', () => {
    expect(scoreSetting(DECOMPOSITION_MODEL, 'zzqqxx')).toBe(0)
    expect(matchesSetting(DECOMPOSITION_MODEL, 'zzqqxx')).toBe(false)
  })

  it('treats an empty query as matching everything equally', () => {
    expect(matchesSetting(DECOMPOSITION_MODEL, '')).toBe(true)
    expect(scoreSetting(DECOMPOSITION_MODEL, '   ')).toBe(
      scoreSetting(AUTH_REVALIDATE, '   '),
    )
  })
})

describe('search ordering', () => {
  const entries = [AUTH_REVALIDATE, DECOMPOSITION_MODEL]

  it('puts the named setting first within its namespace', () => {
    const sameNamespace = [
      makeEntry({ namespace: 'coordination', key: 'wave_timeout_seconds' }),
      DECOMPOSITION_MODEL,
    ]

    const ranked = filterNamespaceEntries(
      sameNamespace,
      'coordination',
      true,
      'decomposition model',
    )

    expect(ranked[0]?.definition.key).toBe('decomposition_model')
  })

  it('puts the namespace holding the best match first', () => {
    // Ranking inside a namespace alone would not fix the report: `api` comes
    // before `coordination` in the fixed order, so the named setting still
    // would not be on the first screen.
    const grouped = filterByNamespace(entries, true, 'decomposition model')

    expect([...grouped.keys()][0]).toBe('coordination')
  })

  it('keeps the fixed order when nothing is being searched', () => {
    // The layout an operator navigates by muscle memory, and re-ordering it
    // on an empty box would take that away for nothing.
    const grouped = filterByNamespace(entries, true, '')

    expect([...grouped.keys()]).toEqual(['api', 'coordination'])
  })

  it('is stable between entries that answer equally well', () => {
    const first = makeEntry({ namespace: 'coordination', key: 'alpha_thing' })
    const second = makeEntry({ namespace: 'coordination', key: 'beta_thing' })

    const ranked = filterNamespaceEntries(
      [first, second],
      'coordination',
      true,
      'thing',
    )

    expect(ranked.map((e) => e.definition.key)).toEqual(['alpha_thing', 'beta_thing'])
  })
})
