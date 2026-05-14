import type { SettingEntry } from '@/api/types/settings'
import {
  entriesToObject,
  serializeEntries,
  detectRemovedKeys,
  buildChanges,
  parseText,
  MAX_EDITOR_BYTES,
} from '@/pages/settings/code-editor-utils'

function makeEntry(overrides: Partial<SettingEntry['definition']> & { value?: string; source?: SettingEntry['source'] } = {}): SettingEntry {
  const { value = '10', source = 'db', ...defOverrides } = overrides
  return {
    definition: {
      namespace: 'api',
      key: 'max_retries',
      type: 'int',
      default: '10',
      description: 'Maximum retry attempts',
      group: 'Execution',
      level: 'basic',
      sensitive: false,
      restart_required: false,
      read_only_post_init: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: null,
      max_value: null,
      ...defOverrides,
    },
    value,
    source,
    updated_at: null,
  }
}

describe('entriesToObject', () => {
  it('groups entries by namespace', () => {
    const entries = [
      makeEntry({ namespace: 'api', key: 'max_retries', value: '10' }),
      makeEntry({ namespace: 'api', key: 'timeout', value: '30' }),
      makeEntry({ namespace: 'budget', key: 'workers', value: '4' }),
    ]
    const result = entriesToObject(entries)
    expect(Object.keys(result)).toEqual(['api', 'budget'])
    expect(result.api).toEqual({ max_retries: '10', timeout: '30' })
    expect(result.budget).toEqual({ workers: '4' })
  })

  it('parses JSON-type values into objects', () => {
    const entries = [
      makeEntry({ namespace: 'api', key: 'urls', type: 'json', value: '["http://a.com"]' }),
    ]
    const result = entriesToObject(entries)
    expect(result.api!.urls).toEqual(['http://a.com'])
  })

  it('falls back to raw string on invalid JSON-type values', () => {
    const entries = [
      makeEntry({ namespace: 'api', key: 'bad', type: 'json', value: 'not-json' }),
    ]
    const result = entriesToObject(entries)
    expect(result.api!.bad).toBe('not-json')
  })
})

describe('serializeEntries', () => {
  const entries = [
    makeEntry({ namespace: 'api', key: 'retries', value: '5' }),
  ]

  it('serializes to JSON', () => {
    const json = serializeEntries(entries, 'json')
    expect(JSON.parse(json)).toEqual({ api: { retries: '5' } })
  })

  it('serializes to YAML', () => {
    const yaml = serializeEntries(entries, 'yaml')
    expect(yaml).toContain('api:')
    expect(yaml).toContain('retries:')
  })
})

describe('detectRemovedKeys', () => {
  it('detects removed keys', () => {
    const original = { api: { a: 1, b: 2 }, engine: { c: 3 } }
    const parsed = { api: { a: 1 } }
    const removed = detectRemovedKeys(original, parsed)
    expect(removed).toContain('api/b')
    expect(removed).toContain('engine/c')
  })

  it('returns empty for no changes', () => {
    const original = { api: { a: 1 } }
    const parsed = { api: { a: 1 } }
    expect(detectRemovedKeys(original, parsed)).toEqual([])
  })

  it('detects entire removed namespaces', () => {
    const original = { api: { a: 1 }, engine: { b: 2 } }
    const parsed = { api: { a: 1 } }
    expect(detectRemovedKeys(original, parsed)).toEqual(['engine/b'])
  })
})

describe('buildChanges', () => {
  it('detects changed values', () => {
    const entryLookup = new Map<string, SettingEntry>([
      ['api/retries', makeEntry({ namespace: 'api', key: 'retries', value: '5' })],
    ])
    const original = { api: { retries: '5' } }
    const parsed = { api: { retries: '10' } }
    const { changes, unknownKeys, envKeys } = buildChanges(parsed, original, entryLookup)
    expect(changes.get('api/retries')).toBe('10')
    expect(unknownKeys).toEqual([])
    expect(envKeys).toEqual([])
  })

  it('flags unknown keys', () => {
    const entryLookup = new Map<string, SettingEntry>()
    const original = {}
    const parsed = { api: { unknown: 'val' } }
    const { unknownKeys } = buildChanges(parsed, original, entryLookup)
    expect(unknownKeys).toEqual(['api/unknown'])
  })

  it('flags env-sourced keys', () => {
    const entryLookup = new Map<string, SettingEntry>([
      ['api/retries', makeEntry({ namespace: 'api', key: 'retries', source: 'env' })],
    ])
    const original = { api: { retries: '5' } }
    const parsed = { api: { retries: '10' } }
    const { envKeys } = buildChanges(parsed, original, entryLookup)
    expect(envKeys).toEqual(['api/retries'])
  })

  it('returns empty changes when values are unchanged', () => {
    const entryLookup = new Map<string, SettingEntry>([
      ['api/retries', makeEntry({ namespace: 'api', key: 'retries', value: '5' })],
    ])
    const original = { api: { retries: '5' } }
    const parsed = { api: { retries: '5' } }
    const { changes } = buildChanges(parsed, original, entryLookup)
    expect(changes.size).toBe(0)
  })
})

describe('parseText', () => {
  it('parses valid JSON', () => {
    const result = parseText('{"api": {"key": "val"}}', 'json')
    expect(result).toEqual({ api: { key: 'val' } })
  })

  it('parses valid YAML', () => {
    const result = parseText('api:\n  key: val', 'yaml')
    expect(result).toEqual({ api: { key: 'val' } })
  })

  it('rejects non-object top level', () => {
    expect(() => parseText('"string"', 'json')).toThrow('object at the top level')
  })

  it('rejects arrays', () => {
    expect(() => parseText('[1, 2]', 'json')).toThrow('object at the top level')
  })

  it('rejects non-object namespace values', () => {
    expect(() => parseText('{"api": "not-object"}', 'json')).toThrow('must be an object')
  })

  it('rejects input exceeding size limit', () => {
    const huge = '{"a": "' + 'x'.repeat(MAX_EDITOR_BYTES) + '"}'
    expect(() => parseText(huge, 'json')).toThrow('too large')
  })

  it('rejects invalid JSON syntax', () => {
    expect(() => parseText('{bad json', 'json')).toThrow()
  })

  it('rejects invalid YAML syntax', () => {
    expect(() => parseText('api:\n  key: [unclosed', 'yaml')).toThrow()
  })

  it('rejects dangerous YAML tags (CORE_SCHEMA safety)', () => {
    // CORE_SCHEMA disables !!js/function and !!js/regexp
    const malicious = '!!js/function "return 1"'
    expect(() => parseText(malicious, 'yaml')).toThrow()
  })
})
