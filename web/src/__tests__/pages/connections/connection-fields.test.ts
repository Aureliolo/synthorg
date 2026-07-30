import { describe, expect, it } from 'vitest'
import type { ConnectionTypeMetadata } from '@/api/types/integrations'
import { webhookSecretFieldFor } from '@/api/types/integrations'
import {
  type ConnectionFieldSpec,
  conditionMet,
  connectionTypeLabel,
  isFieldRequired,
  isFieldVisible,
  metadataGovernsOtherFields,
  resolveConnectionSpec,
  validateConnectionField,
  validateConnectionName,
} from '@/pages/connections/connection-fields'
import { webhookRetentionApplies } from '@/pages/connections/connection-submit'
import type { ConnectionFormState } from '@/pages/connections/connection-form-state'

const databaseMeta: ConnectionTypeMetadata = {
  connection_type: 'database',
  default_auth_method: 'basic_auth',
  label: 'Database',
  description: 'Connect to a SQL database.',
  required_field_names: ['dialect'],
  secret_field_names: ['password'],
  webhook_secret_field: null,
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
      visible_when: null,
      required_when: null,
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
      visible_when: null,
      required_when: null,
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
      visible_when: null,
      required_when: null,
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
  webhook_secret_field: null,
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
      visible_when: null,
      required_when: null,
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
      visible_when: null,
      required_when: null,
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
      visible_when: null,
      required_when: null,
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
      visible_when: null,
      required_when: null,
    },
    {
      name: 'project',
      label: 'Project',
      input_type: 'text',
      placement: 'metadata',
      required: true,
      secret: false,
      options: [],
      placeholder: '',
      help_text: '',
      capture_mode: null,
      visible_when: null,
      required_when: null,
    },
  ],
}

/** A metadata field governing a top-level one, the vendor-preset shape. */
const genericHttpMeta: ConnectionTypeMetadata = {
  connection_type: 'generic_http',
  default_auth_method: 'api_key',
  label: 'Generic HTTP',
  description: 'An HTTP API behind a key.',
  required_field_names: ['token'],
  secret_field_names: ['token', 'signing_secret'],
  webhook_secret_field: 'signing_secret',
  fields: [
    {
      name: 'vendor',
      label: 'Vendor',
      input_type: 'select',
      placement: 'metadata',
      required: true,
      secret: false,
      options: ['example-preset', 'custom'],
      placeholder: '',
      help_text: '',
      capture_mode: null,
      visible_when: null,
      required_when: null,
    },
    {
      name: 'base_url',
      label: 'Base URL',
      input_type: 'url',
      placement: 'base_url',
      required: false,
      secret: false,
      options: [],
      placeholder: '',
      help_text: '',
      capture_mode: null,
      visible_when: { field: 'vendor', values: ['custom'] },
      required_when: { field: 'vendor', values: ['custom'] },
    },
    {
      name: 'token',
      label: 'API Key',
      input_type: 'password',
      placement: 'credential',
      required: true,
      secret: true,
      options: [],
      placeholder: '',
      help_text: '',
      capture_mode: 'masked_field',
      visible_when: null,
      required_when: null,
    },
    {
      name: 'signing_secret',
      label: 'Webhook Signing Secret',
      input_type: 'password',
      placement: 'credential',
      required: false,
      secret: true,
      options: [],
      placeholder: '',
      help_text: '',
      capture_mode: 'masked_field',
      visible_when: { field: 'vendor', values: ['custom'] },
      required_when: null,
    },
  ],
}

/**
 * A registry entry the backend's own validator rejects at import. The
 * resolver still has to fail closed on it: metadata is submitted inline and
 * stored in the clear, so routing this field by placement would persist the
 * raw secret on the connection record.
 */
const secretMetadataMeta: ConnectionTypeMetadata = {
  connection_type: 'deploy',
  default_auth_method: 'bearer_token',
  label: 'Deploy',
  description: 'A deploy target.',
  required_field_names: ['token'],
  secret_field_names: ['token'],
  webhook_secret_field: null,
  fields: [
    {
      name: 'token',
      label: 'Token',
      input_type: 'password',
      placement: 'metadata',
      required: true,
      secret: true,
      options: [],
      placeholder: '',
      help_text: '',
      capture_mode: 'masked_field',
      visible_when: null,
      required_when: null,
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
    // platform/environment/project must NOT land in credentials (that would
    // submit them as secrets and leave the connection metadata empty).
    expect(spec.metadataFields.map((f) => f.key)).toEqual([
      'platform',
      'environment',
      'project',
    ])
    expect(spec.credentialFields.map((f) => f.key)).toEqual(['token'])
    expect(spec.topLevelFields.map((f) => f.key)).toEqual(['base_url'])
  })

  it('covers every required field name with a rendered field', () => {
    const spec = resolveConnectionSpec(deployMeta)
    const rendered = new Set([
      ...spec.topLevelFields.map((f) => f.key),
      ...spec.credentialFields.map((f) => f.key),
      ...spec.metadataFields.map((f) => f.key),
    ])
    for (const name of deployMeta.required_field_names) {
      expect(rendered.has(name)).toBe(true)
    }
  })

  it('keeps a secret field out of metadata whatever its placement claims', () => {
    const spec = resolveConnectionSpec(secretMetadataMeta)
    expect(spec.metadataFields).toEqual([])
    expect(spec.credentialFields.map((f) => f.key)).toEqual(['token'])
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

  it('requires a field only while its backend condition holds', () => {
    // The rule lives in the served metadata, not in a client-side set: the
    // form never decides which dialects need a host.
    const hostSpec: ConnectionFieldSpec = {
      key: 'host',
      label: 'Host',
      type: 'text',
      required: false,
      secret: false,
      requiredWhen: { field: 'dialect', values: ['postgres', 'mysql'] },
    }
    expect(validateConnectionField(hostSpec, '', { dialect: 'postgres' })).toBe(
      'Host is required',
    )
    expect(validateConnectionField(hostSpec, '', { dialect: 'sqlite' })).toBeNull()
    expect(validateConnectionField(hostSpec, '', {})).toBeNull()
  })

  it('skips a hidden field entirely', () => {
    const urlSpec: ConnectionFieldSpec = {
      key: 'base_url',
      label: 'Base URL',
      type: 'url',
      required: true,
      secret: false,
      visibleWhen: { field: 'vendor', values: ['custom'] },
    }
    // Hidden: neither required nor URL-validated, because it does not apply.
    expect(validateConnectionField(urlSpec, '', { vendor: 'example-preset' })).toBeNull()
    expect(validateConnectionField(urlSpec, 'not a url', { vendor: 'example-preset' })).toBeNull()
    expect(validateConnectionField(urlSpec, '', { vendor: 'custom' })).toBe(
      'Base URL is required',
    )
  })
})

describe('conditionMet', () => {
  it('holds when the condition is absent', () => {
    expect(conditionMet(undefined, {})).toBe(true)
  })

  it('trims the compared value', () => {
    expect(conditionMet({ field: 'v', values: ['example-preset'] }, { v: '  example-preset  ' })).toBe(true)
  })

  it('fails for an unset dependency', () => {
    expect(conditionMet({ field: 'v', values: ['example-preset'] }, {})).toBe(false)
  })
})

describe('isFieldRequired', () => {
  const conditional: ConnectionFieldSpec = {
    key: 'host',
    label: 'Host',
    type: 'text',
    required: false,
    secret: false,
    requiredWhen: { field: 'dialect', values: ['postgres'] },
  }

  it('requires the field only while its condition holds', () => {
    expect(isFieldRequired(conditional, { dialect: 'postgres' })).toBe(true)
    expect(isFieldRequired(conditional, { dialect: 'sqlite' })).toBe(false)
  })

  it('never requires a hidden field', () => {
    // A field the operator cannot see must not block submission.
    const hidden: ConnectionFieldSpec = {
      ...conditional,
      required: true,
      visibleWhen: { field: 'vendor', values: ['custom'] },
    }

    expect(isFieldRequired(hidden, { vendor: 'example-preset' })).toBe(false)
    expect(isFieldRequired(hidden, { vendor: 'custom' })).toBe(true)
  })
})

describe('resolveConnectionSpec', () => {
  it('maps both served condition keys onto their camelCase counterparts', () => {
    // Asserted per key rather than through ``metadataGovernsOtherFields``,
    // which is true when EITHER maps: a dropped ``required_when`` would
    // leave every conditional field permanently optional and still satisfy
    // that helper.
    const meta: ConnectionTypeMetadata = {
      ...genericHttpMeta,
      fields: genericHttpMeta.fields.map((field) =>
        field.name === 'base_url'
          ? {
              ...field,
              visible_when: { field: 'vendor', values: ['custom'] },
              required_when: { field: 'vendor', values: ['custom'] },
            }
          : field,
      ),
    }

    const spec = resolveConnectionSpec(meta)
    const baseUrl = spec.topLevelFields.find((f) => f.key === 'base_url')

    expect(baseUrl?.visibleWhen).toEqual({ field: 'vendor', values: ['custom'] })
    expect(baseUrl?.requiredWhen).toEqual({ field: 'vendor', values: ['custom'] })
  })
})

describe('metadataGovernsOtherFields', () => {
  it('is true when a credential depends on a metadata field', () => {
    // Answering the governing field first is what keeps the resulting
    // content shift below the control the operator is using.
    const spec = resolveConnectionSpec(genericHttpMeta)

    expect(metadataGovernsOtherFields(spec)).toBe(true)
  })

  it('is false when the buckets are independent', () => {
    // Leading with metadata there would only push the credential down the
    // tab order for no reason.
    const spec = resolveConnectionSpec(deployMeta)

    expect(metadataGovernsOtherFields(spec)).toBe(false)
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

describe('webhook receipt applicability', () => {
  const registry = [databaseMeta, genericHttpMeta]

  it('names the signing-secret field for a type that can receive webhooks', () => {
    expect(webhookSecretFieldFor('generic_http', registry)).toBe('signing_secret')
  })

  it('names none for a type that cannot', () => {
    expect(webhookSecretFieldFor('database', registry)).toBeNull()
  })

  it('names none before the registry has loaded', () => {
    expect(webhookSecretFieldFor('generic_http', [])).toBeNull()
  })

  // The retention control follows the signing secret's own condition, not just
  // the type: a Generic HTTP connection to a known outbound vendor preset hides
  // its signing secret, so it can never be sent a webhook and must not be
  // offered retention over receipts it cannot accumulate.
  it('hides the signing secret for a preset vendor and shows it for custom', () => {
    const spec = resolveConnectionSpec(genericHttpMeta)
    const field = spec.credentialFields.find((f) => f.key === 'signing_secret')
    expect(field).toBeDefined()
    expect(isFieldVisible(field as ConnectionFieldSpec, { vendor: 'example-preset' })).toBe(
      false,
    )
    expect(isFieldVisible(field as ConnectionFieldSpec, { vendor: 'custom' })).toBe(true)
  })

  it('never requires the signing secret, so outbound-only creation still works', () => {
    const spec = resolveConnectionSpec(genericHttpMeta)
    const field = spec.credentialFields.find((f) => f.key === 'signing_secret')
    expect(isFieldRequired(field as ConnectionFieldSpec, { vendor: 'custom' })).toBe(false)
  })
})

/**
 * The rendered control and the submitted body must gate on one predicate.
 *
 * Gating them differently is how a value typed before a vendor switch rode along
 * invisibly: the control disappeared while the type-level check still said the
 * connection supported webhooks, so the stale value was still sent.
 */
describe('webhookRetentionApplies', () => {
  const registry = [databaseMeta, genericHttpMeta]

  function form(overrides: Partial<ConnectionFormState> = {}): ConnectionFormState {
    return {
      name: 'primary',
      type: 'generic_http',
      topLevel: {},
      credentials: {},
      metadata: { vendor: 'custom' },
      webhookRetention: '',
      sensitive: false,
      allowedRepos: [],
      ...overrides,
    }
  }

  it('applies while the signing secret is visible', () => {
    const spec = resolveConnectionSpec(genericHttpMeta)
    expect(webhookRetentionApplies(form(), spec, registry)).toBe(true)
  })

  it('stops applying once the vendor switches to a preset', () => {
    const spec = resolveConnectionSpec(genericHttpMeta)
    const switched = form({ metadata: { vendor: 'example-preset' } })
    expect(webhookRetentionApplies(switched, spec, registry)).toBe(false)
  })

  it('does not apply to a type that can never receive a webhook', () => {
    const spec = resolveConnectionSpec(databaseMeta)
    expect(webhookRetentionApplies(form({ type: 'database' }), spec, registry)).toBe(false)
  })

  it('does not apply before a type is chosen', () => {
    const spec = resolveConnectionSpec(genericHttpMeta)
    expect(webhookRetentionApplies(form({ type: null }), spec, registry)).toBe(false)
  })

  it('does not apply before the registry has loaded', () => {
    const spec = resolveConnectionSpec(genericHttpMeta)
    expect(webhookRetentionApplies(form(), spec, [])).toBe(false)
  })

  it('ignores a retention value left behind by a vendor switch', () => {
    // The operator typed a value while the control was visible, then switched
    // vendor. Nothing clears the form field, so the predicate is what stops the
    // value being submitted for a connection that cannot accumulate receipts.
    const spec = resolveConnectionSpec(genericHttpMeta)
    const stale = form({
      metadata: { vendor: 'example-preset' },
      webhookRetention: '30',
    })
    expect(webhookRetentionApplies(stale, spec, registry)).toBe(false)
  })
})
