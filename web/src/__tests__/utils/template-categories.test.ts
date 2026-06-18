import {
  categorizeTemplates,
  getCategoryLabel,
  CATEGORY_ORDER,
} from '@/utils/template-categories'
import type { TemplateInfoResponse } from '@/api/types/setup'

const makeTemplate = (
  overrides: Partial<TemplateInfoResponse> = {},
): TemplateInfoResponse => ({
  name: 'test-template',
  display_name: 'Test Template',
  description: 'A test template.',
  source: 'builtin',
  tags: [],
  skill_patterns: [],
  variables: [],
  agent_count: 5,
  department_count: 3,
  autonomy_level: 'semi',
  workflow: 'agile_kanban',
  posture: null,
  ...overrides,
})

describe('categorizeTemplates', () => {
  it('returns empty map for empty input', () => {
    const result = categorizeTemplates([])
    expect(result.size).toBe(0)
  })

  it('groups templates by their primary category tag', () => {
    const templates = [
      makeTemplate({ name: 'startup', tags: ['startup', 'mvp'] }),
      makeTemplate({ name: 'solo', tags: ['solo', 'minimal'] }),
      makeTemplate({ name: 'dev-shop', tags: ['dev-shop', 'agency'] }),
    ]
    const result = categorizeTemplates(templates)

    // 'solo' maps to 'startup' category, so startup has 2 templates
    expect(result.get('startup')).toHaveLength(2)
    expect(result.get('startup')?.[0]?.name).toBe('startup')
    expect(result.get('startup')?.[1]?.name).toBe('solo')
    expect(result.get('dev-shop')).toHaveLength(1)
  })

  it('places templates with no matching category tag under "other"', () => {
    const templates = [
      makeTemplate({ name: 'custom', tags: ['exotic', 'niche'] }),
    ]
    const result = categorizeTemplates(templates)

    expect(result.has('other')).toBe(true)
    expect(result.get('other')).toHaveLength(1)
  })

  it('uses first matching category tag as primary category', () => {
    const templates = [
      makeTemplate({ name: 'multi', tags: ['startup', 'enterprise'] }),
    ]
    const result = categorizeTemplates(templates)

    // Should be in startup (first match), not enterprise
    expect(result.get('startup')).toHaveLength(1)
    expect(result.has('enterprise')).toBe(false)
  })

  it('preserves template order within categories', () => {
    const templates = [
      makeTemplate({ name: 'a', tags: ['startup'] }),
      makeTemplate({ name: 'b', tags: ['startup'] }),
      makeTemplate({ name: 'c', tags: ['startup'] }),
    ]
    const result = categorizeTemplates(templates)
    const names = result.get('startup')!.map((t) => t.name)

    expect(names).toEqual(['a', 'b', 'c'])
  })

  it('returns categories in CATEGORY_ORDER', () => {
    const templates = [
      makeTemplate({ name: 'r', tags: ['research'] }),
      makeTemplate({ name: 's', tags: ['startup'] }),
      makeTemplate({ name: 'd', tags: ['dev-shop'] }),
      makeTemplate({ name: 'e', tags: ['enterprise'] }),
    ]
    const result = categorizeTemplates(templates)
    const keys = [...result.keys()]

    // Derive expected order from fixture tags, not the function under test
    const fixtureCategories = new Set(['research', 'startup', 'dev-shop', 'enterprise'])
    const expectedOrder = CATEGORY_ORDER.filter((c) => fixtureCategories.has(c))
    expect(keys).toEqual(expectedOrder)
  })
})

describe('getCategoryLabel', () => {
  it('returns human-readable labels for known categories', () => {
    expect(getCategoryLabel('startup')).toBe('Startup')
    expect(getCategoryLabel('enterprise')).toBe('Enterprise')
    expect(getCategoryLabel('dev-shop')).toBe('Development')
    expect(getCategoryLabel('research')).toBe('Research')
  })

  it('returns title-cased key for unknown categories', () => {
    expect(getCategoryLabel('other')).toBe('Other')
    expect(getCategoryLabel('exotic')).toBe('Exotic')
  })
})

describe('CATEGORY_ORDER', () => {
  it('is a non-empty readonly array', () => {
    expect(CATEGORY_ORDER.length).toBeGreaterThan(0)
  })

  it('contains no duplicates', () => {
    const unique = new Set(CATEGORY_ORDER)
    expect(unique.size).toBe(CATEGORY_ORDER.length)
  })
})
