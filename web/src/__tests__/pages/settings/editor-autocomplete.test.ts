/**
 * Tests for the settings editor autocomplete extension.
 *
 * Verifies that settingsAutocompleteExtension can be constructed for
 * representative JSON/YAML formats and entry shapes, and returns a
 * truthy CodeMirror Extension for those inputs.
 */

import { settingsAutocompleteExtension } from '@/pages/settings/editor-autocomplete'
import type { SettingEntry } from '@/api/types/settings'
import type { Extension } from '@codemirror/state'

function makeEntry(
  namespace: string,
  key: string,
  overrides: Partial<{
    type: SettingEntry['definition']['type']
    description: string
    enumValues: readonly string[]
  }> = {},
): SettingEntry {
  return {
    definition: {
      namespace: namespace as SettingEntry['definition']['namespace'],
      key,
      type: overrides.type ?? 'str',
      default: '',
      description: overrides.description ?? `${namespace}/${key}`,
      group: 'Test',
      level: 'basic',
      sensitive: false,
      restart_required: false,
      read_only_post_init: false,
      env_var_override: null,
      enum_values: overrides.enumValues ?? [],
      validator_pattern: null,
      min_value: null,
      max_value: null,
      yaml_path: null,
    },
    value: '',
    source: 'db',
    updated_at: null,
  }
}

describe('settingsAutocompleteExtension', () => {
  it('returns an Extension when given valid inputs', () => {
    const entries = [makeEntry('api', 'retries')]
    const ext: Extension = settingsAutocompleteExtension(
      () => 'json',
      () => entries,
    )
    // The extension should be truthy (autocompletion returns an array of extensions)
    expect(ext).toBeTruthy()
  })

  it('returns an Extension for yaml format', () => {
    const entries = [makeEntry('api', 'retries')]
    const ext: Extension = settingsAutocompleteExtension(
      () => 'yaml',
      () => entries,
    )
    expect(ext).toBeTruthy()
  })

  it('returns an Extension with empty entries', () => {
    const ext: Extension = settingsAutocompleteExtension(
      () => 'json',
      () => [],
    )
    expect(ext).toBeTruthy()
  })

  it('accepts entries with enum values', () => {
    const entries = [
      makeEntry('api', 'log_level', {
        type: 'enum',
        enumValues: ['debug', 'info', 'warning', 'error'],
      }),
    ]
    const ext: Extension = settingsAutocompleteExtension(
      () => 'json',
      () => entries,
    )
    expect(ext).toBeTruthy()
  })

  it('accepts multiple namespaces', () => {
    const entries = [
      makeEntry('api', 'retries'),
      makeEntry('api', 'timeout'),
      makeEntry('budget', 'cap'),
      makeEntry('security', 'level'),
    ]
    const ext: Extension = settingsAutocompleteExtension(
      () => 'json',
      () => entries,
    )
    expect(ext).toBeTruthy()
  })
})
