/**
 * Settings mock-data builders.
 */

export interface MockSettingValue {
  namespace: string
  key: string
  value: unknown
  source: 'default' | 'yaml' | 'env' | 'db'
  compose_set: boolean
}

export function makeSetting(
  overrides: Partial<MockSettingValue> = {},
): MockSettingValue {
  return {
    namespace: 'api',
    key: 'rate_limit_per_minute',
    value: 60,
    source: 'default',
    compose_set: false,
    ...overrides,
  }
}
