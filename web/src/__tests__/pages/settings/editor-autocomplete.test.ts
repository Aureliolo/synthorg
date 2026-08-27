/**
 * Tests for the settings editor autocomplete extension.
 *
 * Verifies that settingsAutocompleteExtension can be constructed for
 * representative JSON/YAML formats and entry shapes, and returns a
 * truthy CodeMirror Extension for those inputs.
 */

import { makeSettingEntry } from '@/__tests__/helpers/factories'
import { settingsAutocompleteExtension } from '@/pages/settings/editor-autocomplete'
import type { SettingEntry } from '@/api/types/settings'
import type { Extension } from '@codemirror/state'

// Autocomplete is keyed on the namespace/key pair, so those two stay
// positional here and the description defaults to naming the pair back.
function makeEntry(
  namespace: SettingEntry['definition']['namespace'],
  key: string,
  overrides: Partial<{
    type: SettingEntry['definition']['type']
    description: string
    enumValues: readonly string[]
  }> = {},
): SettingEntry {
  return makeSettingEntry({
    namespace,
    key,
    type: overrides.type ?? 'str',
    default: '',
    description: overrides.description ?? `${namespace}/${key}`,
    group: 'Test',
    enum_values: overrides.enumValues ?? [],
    value: '',
    source: 'db',
  })
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
