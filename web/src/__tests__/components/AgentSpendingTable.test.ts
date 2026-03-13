import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import AgentSpendingTable from '@/components/budget/AgentSpendingTable.vue'
import type { CostRecord } from '@/api/types'

const DataTableStub = defineComponent({
  name: 'DataTable',
  props: ['value'],
  template: '<table><slot /></table>',
})

const ColumnStub = defineComponent({
  name: 'PvColumn',
  props: ['field', 'header'],
  template: '<td />',
})

const mockRecords: CostRecord[] = [
  {
    agent_id: 'agent-1',
    task_id: 't1',
    model: 'm1',
    provider: 'p1',
    input_tokens: 100,
    output_tokens: 50,
    cost_usd: 0.005,
    timestamp: '2026-03-12T10:00:00Z',
    call_category: null,
  },
  {
    agent_id: 'agent-1',
    task_id: 't2',
    model: 'm1',
    provider: 'p1',
    input_tokens: 200,
    output_tokens: 100,
    cost_usd: 0.01,
    timestamp: '2026-03-12T11:00:00Z',
    call_category: null,
  },
  {
    agent_id: 'agent-2',
    task_id: 't3',
    model: 'm1',
    provider: 'p1',
    input_tokens: 50,
    output_tokens: 25,
    cost_usd: 0.002,
    timestamp: '2026-03-12T12:00:00Z',
    call_category: null,
  },
]

function mountTable(records: CostRecord[] = mockRecords) {
  return mount(AgentSpendingTable, {
    props: { records },
    global: {
      stubs: {
        DataTable: DataTableStub,
        Column: ColumnStub,
      },
    },
  })
}

describe('AgentSpendingTable', () => {
  it('aggregates records by agent into summaries', () => {
    const wrapper = mountTable()

    const table = wrapper.findComponent(DataTableStub)
    const summaries = table.props('value') as Array<{
      agent_id: string
      total_cost_usd: number
      record_count: number
      total_input_tokens: number
      total_output_tokens: number
    }>

    expect(summaries).toHaveLength(2)

    // Two records for agent-1 should be merged into one summary
    const agent1 = summaries.find((s) => s.agent_id === 'agent-1')
    expect(agent1).toBeDefined()
    expect(agent1!.total_cost_usd).toBeCloseTo(0.015, 6)
    expect(agent1!.record_count).toBe(2)
    expect(agent1!.total_input_tokens).toBe(300)
    expect(agent1!.total_output_tokens).toBe(150)

    // Single record for agent-2
    const agent2 = summaries.find((s) => s.agent_id === 'agent-2')
    expect(agent2).toBeDefined()
    expect(agent2!.total_cost_usd).toBeCloseTo(0.002, 6)
    expect(agent2!.record_count).toBe(1)
    expect(agent2!.total_input_tokens).toBe(50)
    expect(agent2!.total_output_tokens).toBe(25)
  })

  it('sorts summaries by total_cost descending', () => {
    const wrapper = mountTable()

    const table = wrapper.findComponent(DataTableStub)
    const summaries = table.props('value') as Array<{
      agent_id: string
      total_cost_usd: number
    }>

    expect(summaries[0].agent_id).toBe('agent-1')
    expect(summaries[1].agent_id).toBe('agent-2')
    expect(summaries[0].total_cost_usd).toBeGreaterThan(summaries[1].total_cost_usd)
  })

  it('returns empty summaries for empty records', () => {
    const wrapper = mountTable([])

    const table = wrapper.findComponent(DataTableStub)
    const summaries = table.props('value') as unknown[]

    expect(summaries).toHaveLength(0)
  })
})
