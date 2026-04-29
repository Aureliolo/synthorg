/**
 * Setup-wizard mock-data builders.
 */

export interface MockSetupConfig {
  company_name: string
  company_domain: string
  initial_agents: number
  theme: 'system' | 'light' | 'dark'
  providers: string[]
}

export function makeSetupConfig(
  overrides: Partial<MockSetupConfig> = {},
): MockSetupConfig {
  return {
    company_name: 'ExampleCorp',
    company_domain: 'example.test',
    initial_agents: 3,
    theme: 'system',
    providers: ['example-provider'],
    ...overrides,
  }
}
