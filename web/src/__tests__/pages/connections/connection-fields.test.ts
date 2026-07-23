import { describe, expect, it } from 'vitest'
import type { ConnectionTypeMetadata } from '@/api/types/integrations'
import {
  type ConnectionFieldSpec,
  connectionTypeLabel,
  resolveConnectionSpec,
  validateA2APeerCredentials,
  validateConnectionField,
  validateConnectionName,
} from '@/pages/connections/connection-fields'

const databaseMeta: ConnectionTypeMetadata = {
  connection_type: 'database',
  default_auth_method: 'basic_auth',
  label: 'Database',
  description: 'Connect to a SQL database.',
  required_field_names: ['dialect'],
  secret_field_names: ['password'],
  fields: [
    {
      name: 'base_url',
      label: 'URL',
      input_type: 'url',
      placement: 'base_url',
      required: false,
      secret: false,
      options: [],
      placeholder: '',
      help_text: '',
      capture_mode: null,
    },
    {
      name: 'dialect',
      label: 'Dialect',
      input_type: 'select',
      placement: 'credential',
      required: true,
      secret: false,
      options: ['postgres', 'mysql', 'sqlite'],
      placeholder: '',
      help_text: '',
      capture_mode: null,
    },
    {
      name: 'password',
      label: 'Password',
      input_type: 'password',
      placement: 'credential',
      required: false,
      secret: true,
      options: [],
      placeholder: '',
      help_text: '',
      capture_mode: 'masked_field',
    },
  ],
}

const deployMeta: ConnectionTypeMetadata = {
  connection_type: 'deploy',
  default_auth_method: 'bearer_token',
  label: 'Deploy',
  description: 'A deploy target.',
  required_field_names: ['token', 'base_url', 'platform', 'project'],
  secret_field_names: ['token'],
  fields: [
    {
      name: 'token',
      label: 'Token',
      input_type: 'password',
      placement: 'credential',
      required: true,
      secret: true,
      options: [],
      placeholder: '',
      help_text: '',
      capture_mode: 'masked_field',
    },
    {
      name: 'base_url',
      label: 'API URL',
      input_type: 'url',
      placement: 'base_url',
      required: true,
      secret: false,
      options: [],
      placeholder: '',
      help_text: '',
      capture_mode: null,
    },
    {
      name: 'platform',
      label: 'Platform',
      input_type: 'select',
      placement: 'metadata',
      required: true,
      secret: false,
      options: ['vercel'],
      placeholder: '',
      help_text: '',
      capture_mode: null,
    },
    {
      name: 'environment',
      label: 'Environment',
      input_type: 'select',
      placement: 'metadata',
      required: false,
      secret: false,
      options: ['staging', 'production'],
      placeholder: '',
      help_text: '',
      capture_mode: null,
    },
  ],
}

describe('resolveConnectionSpec', () => {
  it('splits fields by placement and carries the secret flag', () => {
    const spec = resolveConnectionSpec(databaseMeta)
    expect(spec.label).toBe('Database')
    expect(spec.defaultAuthMethod).toBe('basic_auth')
    expect(spec.topLevelFields.map((f) => f.key)).toEqual(['base_url'])
    expect(spec.credentialFields.map((f) => f.key)).toEqual(['dialect', 'password'])
    expect(spec.metadataFields).toEqual([])
    const password = spec.credentialFields.find((f) => f.key === 'password')
    expect(password?.secret).toBe(true)
    const dialect = spec.credentialFields.find((f) => f.key === 'dialect')
    expect(dialect?.secret).toBe(false)
    expect(dialect?.options).toEqual(['postgres', 'mysql', 'sqlite'])
  })

  it('routes metadata-placement fields into their own bucket', () => {
    const spec = resolveConnectionSpec(deployMeta)
    // platform/environment must NOT land in credentials (that would submit
    // them as secrets and leave the connection metadata empty).
    expect(spec.metadataFields.map((f) => f.key)).toEqual(['platform', 'environment'])
    expect(spec.credentialFields.map((f) => f.key)).toEqual(['token'])
    expect(spec.topLevelFields.map((f) => f.key)).toEqual(['base_url'])
  })
})

describe('validateConnectionField', () => {
  const dialectSpec = resolveConnectionSpec(databaseMeta).credentialFields.find(
    (f) => f.key === 'dialect',
  )!

  it('accepts a dialect from the metadata options (backend contract)', () => {
    // The backend dialect is ``postgres`` (not ``postgresql``); the form now
    // validates against the field's own options, so no drift.
    expect(validateConnectionField(dialectSpec, 'postgres')).toBeNull()
  })

  it('rejects a value outside the metadata options', () => {
    expect(validateConnectionField(dialectSpec, 'oracle')).toMatch(/must be one of/)
  })

  it('reports the required error for an empty required field', () => {
    expect(validateConnectionField(dialectSpec, '   ')).toBe('Dialect is required')
  })

  it('requires database server fields for a networked dialect but not sqlite', () => {
    const hostSpec: ConnectionFieldSpec = {
      key: 'host',
      label: 'Host',
      type: 'text',
      required: false,
      secret: false,
    }
    expect(validateConnectionField(hostSpec, '', 'postgres')).toBe('Host is required')
    expect(validateConnectionField(hostSpec, '', 'sqlite')).toBeNull()
  })
})

describe('validateA2APeerCredentials', () => {
  it('requires the api_key for the api_key scheme', () => {
    const errors = validateA2APeerCredentials('api_key', {})
    expect(errors['api_key']).toMatch(/Required/)
  })

  it('passes when the scheme requirements are met', () => {
    const errors = validateA2APeerCredentials('bearer', { access_token: 'tok' })
    expect(errors).toEqual({})
  })
})

describe('validateConnectionName', () => {
  it('rejects blank and invalid names, accepts a slug', () => {
    expect(validateConnectionName('')).toBe('Name is required')
    expect(validateConnectionName('has spaces')).toMatch(/may only contain/)
    expect(validateConnectionName('primary-github_1')).toBeNull()
  })
})

describe('connectionTypeLabel', () => {
  it('uses the registry label when loaded', () => {
    expect(connectionTypeLabel('database', [databaseMeta])).toBe('Database')
  })

  it('humanizes the enum as a fallback before metadata loads', () => {
    expect(connectionTypeLabel('generic_http', [])).toBe('Generic Http')
  })
})
