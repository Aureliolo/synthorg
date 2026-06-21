/**
 * Pure-function coverage for the provider form helpers: request building
 * for every auth type (including the oauth / custom_header fields that
 * previously caused silent credential loss), validation, and the
 * vendor-neutral copy contract.
 */
import { describe, expect, it } from 'vitest'
import type { CloudPreset } from '@/api/types/providers'
import {
  buildCreateProviderRequest,
  buildUpdateProviderRequest,
  computeAvailableAuthTypes,
  computeProviderValidation,
  subscriptionTokenHint,
  validateOptionalUrl,
  validateProviderName,
  type ProviderFormValues,
} from '../provider-form-helpers'

function cloudPreset(authTypes: CloudPreset['supported_auth_types']): CloudPreset {
  return {
    kind: 'cloud',
    name: 'example-provider',
    display_name: 'Example Provider',
    description: '',
    driver: 'litellm',
    litellm_provider: 'example-provider',
    auth_type: 'api_key',
    supported_auth_types: authTypes,
    default_base_url: null,
    requires_base_url: false,
    is_featured: true,
    default_models: [],
  }
}

function values(overrides: Partial<ProviderFormValues> = {}): ProviderFormValues {
  return {
    name: 'my-provider',
    authType: 'api_key',
    apiKey: '',
    subscriptionToken: '',
    customHeaderName: '',
    customHeaderValue: '',
    oauthTokenUrl: '',
    oauthClientId: '',
    oauthClientSecret: '',
    oauthScope: '',
    baseUrl: '',
    litellmProvider: '',
    tosAccepted: false,
    ...overrides,
  }
}

describe('buildCreateProviderRequest', () => {
  it('carries custom_header credentials when the auth type is custom_header', () => {
    const req = buildCreateProviderRequest(
      values({ authType: 'custom_header', customHeaderName: 'X-Key', customHeaderValue: 'secret' }),
    )
    expect(req.custom_header_name).toBe('X-Key')
    expect(req.custom_header_value).toBe('secret')
  })

  it('carries oauth credentials when the auth type is oauth', () => {
    const req = buildCreateProviderRequest(
      values({
        authType: 'oauth',
        oauthTokenUrl: 'https://auth.example.com/token',
        oauthClientId: 'cid',
        oauthClientSecret: 'csecret',
        oauthScope: 'read',
      }),
    )
    expect(req.oauth_token_url).toBe('https://auth.example.com/token')
    expect(req.oauth_client_id).toBe('cid')
    expect(req.oauth_client_secret).toBe('csecret')
    expect(req.oauth_scope).toBe('read')
  })

  it('omits credential fields for unrelated auth types', () => {
    const req = buildCreateProviderRequest(values({ authType: 'api_key', apiKey: 'k' }))
    expect(req.custom_header_name).toBeUndefined()
    expect(req.oauth_token_url).toBeUndefined()
    expect(req.api_key).toBe('k')
  })
})

describe('buildUpdateProviderRequest', () => {
  it('preserves an existing custom-header secret when the value field is left blank', () => {
    const req = buildUpdateProviderRequest(
      values({ authType: 'custom_header', customHeaderName: 'X-Key', customHeaderValue: '' }),
    )
    expect(req.custom_header_name).toBe('X-Key')
    expect('custom_header_value' in req).toBe(false)
  })

  it('preserves an existing oauth secret when the secret field is left blank', () => {
    const req = buildUpdateProviderRequest(
      values({ authType: 'oauth', oauthClientId: 'cid', oauthClientSecret: '' }),
    )
    expect(req.oauth_client_id).toBe('cid')
    expect('oauth_client_secret' in req).toBe(false)
  })

  it('sends a re-typed oauth secret', () => {
    const req = buildUpdateProviderRequest(
      values({ authType: 'oauth', oauthClientSecret: 'new-secret' }),
    )
    expect(req.oauth_client_secret).toBe('new-secret')
  })
})

describe('validateProviderName', () => {
  it('accepts lowercase / digits / hyphens', () => {
    expect(validateProviderName('my-provider-1')).toBeNull()
  })

  it('rejects uppercase and spaces', () => {
    expect(validateProviderName('My Provider')).not.toBeNull()
  })

  it('rejects an empty name', () => {
    expect(validateProviderName('   ')).not.toBeNull()
  })
})

describe('validateOptionalUrl', () => {
  it('allows an empty value', () => {
    expect(validateOptionalUrl('')).toBeNull()
  })

  it('accepts an https URL', () => {
    expect(validateOptionalUrl('https://api.example.com/v1')).toBeNull()
  })

  it('rejects a non-URL', () => {
    expect(validateOptionalUrl('not a url')).not.toBeNull()
  })
})

describe('computeProviderValidation', () => {
  it('blocks submit when an api_key create has no key', () => {
    const result = computeProviderValidation({
      mode: 'create',
      values: values({ authType: 'api_key', apiKey: '' }),
      preset: undefined,
      submitting: false,
    })
    expect(result.apiKeyMissing).toBe(true)
    expect(result.canSubmit).toBe(false)
  })

  it('allows submit once an api key is supplied', () => {
    const result = computeProviderValidation({
      mode: 'create',
      values: values({ authType: 'api_key', apiKey: 'sk-test' }),
      preset: undefined,
      submitting: false,
    })
    expect(result.canSubmit).toBe(true)
  })

  it('surfaces a name format error inline', () => {
    const result = computeProviderValidation({
      mode: 'create',
      values: values({ name: 'Bad Name', apiKey: 'k' }),
      preset: undefined,
      submitting: false,
    })
    expect(result.fieldErrors.name).not.toBeNull()
    expect(result.canSubmit).toBe(false)
  })

  it('blocks a custom_header create with a missing name or value', () => {
    const blocked = computeProviderValidation({
      mode: 'create',
      values: values({ authType: 'custom_header', customHeaderName: '', customHeaderValue: '' }),
      preset: undefined,
      submitting: false,
    })
    expect(blocked.canSubmit).toBe(false)
    const ok = computeProviderValidation({
      mode: 'create',
      values: values({ authType: 'custom_header', customHeaderName: 'X-Key', customHeaderValue: 'v' }),
      preset: undefined,
      submitting: false,
    })
    expect(ok.canSubmit).toBe(true)
  })

  it('blocks an oauth create missing token URL, client id, or secret', () => {
    const blocked = computeProviderValidation({
      mode: 'create',
      values: values({ authType: 'oauth', oauthTokenUrl: '', oauthClientId: '', oauthClientSecret: '' }),
      preset: undefined,
      submitting: false,
    })
    expect(blocked.canSubmit).toBe(false)
    const ok = computeProviderValidation({
      mode: 'create',
      values: values({
        authType: 'oauth',
        oauthTokenUrl: 'https://auth.example.com/token',
        oauthClientId: 'cid',
        oauthClientSecret: 'csecret',
      }),
      preset: undefined,
      submitting: false,
    })
    expect(ok.canSubmit).toBe(true)
  })

  it('allows an oauth edit with a blank secret (keeps the stored secret)', () => {
    const result = computeProviderValidation({
      mode: 'edit',
      values: values({
        authType: 'oauth',
        oauthTokenUrl: 'https://auth.example.com/token',
        oauthClientId: 'cid',
        oauthClientSecret: '',
      }),
      preset: undefined,
      submitting: false,
    })
    expect(result.canSubmit).toBe(true)
  })

  it('blocks a subscription create with a blank token (once ToS accepted)', () => {
    const result = computeProviderValidation({
      mode: 'create',
      values: values({ authType: 'subscription', subscriptionToken: '', tosAccepted: true }),
      preset: undefined,
      submitting: false,
    })
    expect(result.canSubmit).toBe(false)
  })
})

describe('computeAvailableAuthTypes', () => {
  it('drops oauth / custom_header for a cloud preset (the preset-create request cannot carry them)', () => {
    const options = computeAvailableAuthTypes(
      cloudPreset(['api_key', 'subscription', 'oauth', 'custom_header']),
      undefined,
    )
    const values = options.map((o) => o.value)
    expect(values).toContain('api_key')
    expect(values).toContain('subscription')
    expect(values).not.toContain('oauth')
    expect(values).not.toContain('custom_header')
  })

  it('offers every auth type for a custom endpoint (no preset)', () => {
    const values = computeAvailableAuthTypes(null, undefined).map((o) => o.value)
    expect(values).toContain('oauth')
    expect(values).toContain('custom_header')
  })
})

describe('subscriptionTokenHint (vendor-neutral copy)', () => {
  const BANNED_VENDOR_NAMES = ['claude', 'anthropic', 'openai', 'gpt']

  it('names the provider via display_name without a vendor reference', () => {
    const hint = subscriptionTokenHint('Example Provider')
    expect(hint).toContain('Example Provider')
    expect(hint).not.toContain('setup-token')
    for (const vendor of BANNED_VENDOR_NAMES) {
      expect(hint.toLowerCase()).not.toContain(vendor)
    }
  })

  it('falls back to a generic phrase with no display name', () => {
    const hint = subscriptionTokenHint(undefined).toLowerCase()
    for (const vendor of BANNED_VENDOR_NAMES) {
      expect(hint).not.toContain(vendor)
    }
  })
})
