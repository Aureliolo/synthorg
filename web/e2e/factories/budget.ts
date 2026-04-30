/**
 * Budget mock-data builders.
 */

export interface MockBudgetSnapshot {
  total_cost: number
  budget_remaining: number
  budget_used_percent: number
  currency: string
}

export function makeBudgetSnapshot(
  overrides: Partial<MockBudgetSnapshot> = {},
): MockBudgetSnapshot {
  return {
    total_cost: 127.43,
    budget_remaining: 372.57,
    budget_used_percent: 25.5,
    currency: 'USD',
    ...overrides,
  }
}

export interface MockProviderCost {
  provider_name: string
  total_cost: number
  request_count: number
}

export function makeProviderCost(
  overrides: Partial<MockProviderCost> = {},
): MockProviderCost {
  return {
    provider_name: 'example-provider',
    total_cost: 23.45,
    request_count: 100,
    ...overrides,
  }
}
