import { apiClient, unwrap, unwrapPaginated } from '../client'
import type { AgentSpending, BudgetConfig, CostRecord, PaginationParams } from '../types'

export async function getBudgetConfig(): Promise<BudgetConfig> {
  const response = await apiClient.get('/budget/config')
  return unwrap(response)
}

export async function listCostRecords(
  params?: PaginationParams & { agent_id?: string; task_id?: string },
) {
  const response = await apiClient.get('/budget/records', { params })
  return unwrapPaginated<CostRecord>(response)
}

export async function getAgentSpending(agentId: string): Promise<AgentSpending> {
  const response = await apiClient.get(`/budget/agents/${encodeURIComponent(agentId)}`)
  return unwrap(response)
}
