import { describe, expect, it } from 'vitest'
import {
  canonicalizeDialect,
  validateConnectionField,
} from '@/pages/connections/connection-type-fields'
import type { ConnectionFieldSpec } from '@/pages/connections/connection-type-fields'

const dialectSpec: ConnectionFieldSpec = {
  key: 'dialect',
  label: 'Dialect',
  type: 'text',
  required: true,
}

describe('canonicalizeDialect', () => {
  it('trims and lowercases so the wire form matches the backend contract', () => {
    expect(canonicalizeDialect(' SQLite ')).toBe('sqlite')
    expect(canonicalizeDialect('PostgreSQL')).toBe('postgresql')
    expect(canonicalizeDialect('mysql')).toBe('mysql')
  })
})

describe('validateConnectionField dialect path', () => {
  it('accepts a supported dialect case-insensitively with surrounding space', () => {
    expect(validateConnectionField(dialectSpec, ' SQLite ')).toBeNull()
  })

  it('rejects a value outside the supported set', () => {
    expect(validateConnectionField(dialectSpec, 'postgres')).toMatch(
      /must be one of/,
    )
  })

  it('reports the required error for an empty dialect', () => {
    expect(validateConnectionField(dialectSpec, '   ')).toBe('Dialect is required')
  })
})
