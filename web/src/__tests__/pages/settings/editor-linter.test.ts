import { buildSchemaInfo, validateSchema } from '@/pages/settings/editor-linter'
import type { SettingEntry } from '@/api/types/settings'

function makeEntry(
  namespace: string,
  key: string,
  type: SettingEntry['definition']['type'] = 'str',
): SettingEntry {
  return {
    definition: {
      namespace: namespace as SettingEntry['definition']['namespace'],
      key,
      type,
      default: '',
      description: `${namespace}/${key}`,
      group: 'Test',
      level: 'basic',
      sensitive: false,
      restart_required: false,
      read_only_post_init: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: null,
      max_value: null,
    },
    value: '',
    source: 'db',
    updated_at: null,
  }
}

describe('buildSchemaInfo', () => {
  it('builds namespace and key sets from entries', () => {
    const entries = [
      makeEntry('api', 'max_retries', 'int'),
      makeEntry('api', 'timeout', 'int'),
      makeEntry('engine', 'workers', 'int'),
    ]
    const schema = buildSchemaInfo(entries)
    expect(schema.knownNamespaces).toEqual(new Set(['api', 'engine']))
    expect(schema.namespaceKeys.get('api')).toEqual(new Set(['max_retries', 'timeout']))
    expect(schema.namespaceKeys.get('engine')).toEqual(new Set(['workers']))
    expect(schema.keyTypes.get('api/max_retries')).toBe('int')
  })

  it('returns empty sets for empty entries', () => {
    const schema = buildSchemaInfo([])
    expect(schema.knownNamespaces.size).toBe(0)
    expect(schema.namespaceKeys.size).toBe(0)
  })
})

describe('validateSchema', () => {
  const entries = [
    makeEntry('api', 'max_retries'),
    makeEntry('api', 'timeout'),
    makeEntry('engine', 'workers'),
  ]
  const schema = buildSchemaInfo(entries)

  it('returns no diagnostics for valid JSON', () => {
    const text = '{"api": {"max_retries": 10, "timeout": 30}, "engine": {"workers": 4}}'
    const parsed = JSON.parse(text) as Record<string, Record<string, unknown>>
    expect(validateSchema(parsed, schema, text, 'json')).toEqual([])
  })

  it('flags unknown namespace in JSON with correct position', () => {
    const text = '{"unknown_ns": {"key": "val"}}'
    const parsed = JSON.parse(text) as Record<string, Record<string, unknown>>
    const diagnostics = validateSchema(parsed, schema, text, 'json')
    expect(diagnostics).toHaveLength(1)
    expect(diagnostics[0]!.message).toContain('Unknown namespace')
    expect(diagnostics[0]!.severity).toBe('warning')
    // "unknown_ns" starts at index 1 (after opening {"), spans 10 chars + 2 quotes
    expect(diagnostics[0]!.from).toBe(1)
    expect(diagnostics[0]!.to).toBe(13)
  })

  it('flags unknown key within known namespace in JSON with correct position', () => {
    const text = '{"api": {"max_retries": 10, "bogus_key": true}}'
    const parsed = JSON.parse(text) as Record<string, Record<string, unknown>>
    const diagnostics = validateSchema(parsed, schema, text, 'json')
    expect(diagnostics).toHaveLength(1)
    expect(diagnostics[0]!.message).toContain('Unknown setting key "bogus_key"')
    // "bogus_key" appears after the api namespace scope
    expect(diagnostics[0]!.from).toBe(28)
    expect(diagnostics[0]!.to).toBe(39)
  })

  it('flags unknown namespace in YAML', () => {
    const text = 'unknown_ns:\n  key: val'
    const parsed = { unknown_ns: { key: 'val' } }
    const diagnostics = validateSchema(parsed, schema, text, 'yaml')
    expect(diagnostics).toHaveLength(1)
    expect(diagnostics[0]!.message).toContain('Unknown namespace')
  })

  it('flags unknown key within known namespace in YAML', () => {
    const text = 'api:\n  bogus_key: true'
    const parsed = { api: { bogus_key: true } }
    const diagnostics = validateSchema(parsed, schema, text, 'yaml')
    expect(diagnostics).toHaveLength(1)
    expect(diagnostics[0]!.message).toContain('Unknown setting key "bogus_key"')
  })

  it('handles multiple errors', () => {
    const text = '{"bad_ns": {"a": 1}, "api": {"bad_key": 2}}'
    const parsed = JSON.parse(text) as Record<string, Record<string, unknown>>
    const diagnostics = validateSchema(parsed, schema, text, 'json')
    expect(diagnostics).toHaveLength(2)
  })
})
