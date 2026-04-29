/**
 * Provider mock-data builders.
 */

export interface MockProvider {
  id: string
  name: string
  family: string
  enabled: boolean
  health: 'up' | 'degraded' | 'down' | 'unknown'
}

export function makeProvider(overrides: Partial<MockProvider> = {}): MockProvider {
  return {
    id: 'provider-001',
    name: 'example-provider',
    family: 'example',
    enabled: true,
    health: 'up',
    ...overrides,
  }
}

export interface MockProviderHealth {
  provider_name: string
  status: 'up' | 'degraded' | 'down' | 'unknown'
  latency_ms: number | null
  error_detail: string | null
  checked_at: string
}

export function makeProviderHealth(
  overrides: Partial<MockProviderHealth> = {},
): MockProviderHealth {
  return {
    provider_name: 'example-provider',
    status: 'up',
    latency_ms: 42,
    error_detail: null,
    checked_at: '2026-04-01T12:00:00Z',
    ...overrides,
  }
}
