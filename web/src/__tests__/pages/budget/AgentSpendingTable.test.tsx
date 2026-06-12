import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AgentSpendingTable } from '@/pages/budget/AgentSpendingTable'
import { formatCurrency } from '@/utils/format'
import type { AgentSpendingRow } from '@/utils/budget'

function makeRows(count: number): AgentSpendingRow[] {
  return Array.from({ length: count }, (_, i) => ({
    agentId: `agent-${i}`,
    agentName: `Agent ${String.fromCharCode(65 + i)}`,
    totalCost: (count - i) * 10,
    budgetPercent: ((count - i) * 10) / count,
    taskCount: 3 + i,
    costPerTask: ((count - i) * 10) / (3 + i),
  }))
}

describe('AgentSpendingTable', () => {
  it('renders section title', () => {
    render(<AgentSpendingTable rows={[]} />)
    expect(screen.getByText('Agent Spending')).toBeInTheDocument()
  })

  it('shows empty state when no rows', () => {
    render(<AgentSpendingTable rows={[]} />)
    expect(screen.getByText('No agent spending data')).toBeInTheDocument()
    expect(screen.getByText('Cost records will appear as agents consume tokens.')).toBeInTheDocument()
  })

  it('renders correct number of data rows', () => {
    const rows = makeRows(4)
    render(<AgentSpendingTable rows={rows} />)
    for (const row of rows) {
      expect(screen.getByText(row.agentName)).toBeInTheDocument()
    }
    // Verify exact count -- no extra rows rendered
    const agentCells = screen.getAllByText(/^Agent [A-Z]$/)
    expect(agentCells).toHaveLength(4)
  })

  it('renders all column headers', () => {
    render(<AgentSpendingTable rows={makeRows(2)} />)
    expect(screen.getByText('Agent')).toBeInTheDocument()
    expect(screen.getByText('Total Cost')).toBeInTheDocument()
    expect(screen.getByText('% of Budget')).toBeInTheDocument()
    expect(screen.getByText('Tasks')).toBeInTheDocument()
    expect(screen.getByText('Cost/Task')).toBeInTheDocument()
  })

  it('formats cost columns with currency', () => {
    const rows: AgentSpendingRow[] = [{
      agentId: 'a1',
      agentName: 'Alice',
      totalCost: 42.5,
      budgetPercent: 25,
      taskCount: 5,
      costPerTask: 8.5,
    }]
    render(<AgentSpendingTable rows={rows} currency="USD" />)
    expect(screen.getByText(formatCurrency(42.5, 'USD'))).toBeInTheDocument()
    expect(screen.getByText(formatCurrency(8.5, 'USD'))).toBeInTheDocument()
  })

  it('formats budget percent with one decimal', () => {
    const rows: AgentSpendingRow[] = [{
      agentId: 'a1',
      agentName: 'Alice',
      totalCost: 10,
      budgetPercent: 33.333,
      taskCount: 2,
      costPerTask: 5,
    }]
    render(<AgentSpendingTable rows={rows} />)
    expect(screen.getByText('33.3%')).toBeInTheDocument()
  })

  it('displays task count as a number', () => {
    const rows: AgentSpendingRow[] = [{
      agentId: 'a1',
      agentName: 'Alice',
      totalCost: 10,
      budgetPercent: 10,
      taskCount: 7,
      costPerTask: 1.43,
    }]
    render(<AgentSpendingTable rows={rows} />)
    expect(screen.getByText('7')).toBeInTheDocument()
  })

  it('defaults sort to totalCost descending', () => {
    const totalCostHeader = () => screen.getByText('Total Cost').closest('button')
    render(<AgentSpendingTable rows={makeRows(3)} />)
    expect(totalCostHeader()).toHaveAttribute(
      'aria-label',
      expect.stringContaining('sorted descending'),
    )
  })

  it('toggles sort direction on same column click', async () => {
    const user = userEvent.setup()
    render(<AgentSpendingTable rows={makeRows(3)} />)
    const totalCostBtn = screen.getByText('Total Cost').closest('button')!
    expect(totalCostBtn).toHaveAttribute('aria-label', expect.stringContaining('sorted descending'))

    await user.click(totalCostBtn)
    expect(totalCostBtn).toHaveAttribute('aria-label', expect.stringContaining('sorted ascending'))

    // Verify DOM order is ascending (cheapest first)
    const agentCells = screen.getAllByText(/^Agent [A-Z]$/)
    const agentNames = agentCells.map((el) => el.textContent)
    expect(agentNames).toEqual(['Agent C', 'Agent B', 'Agent A'])
  })

  it('defaults to ascending for text column (Agent)', async () => {
    const user = userEvent.setup()
    render(<AgentSpendingTable rows={makeRows(3)} />)

    const agentBtn = screen.getByText('Agent').closest('button')!
    await user.click(agentBtn)
    expect(agentBtn).toHaveAttribute('aria-label', expect.stringContaining('sorted ascending'))
    // Previous column drops the sorted state from its accessible name
    const totalCostBtn = screen.getByText('Total Cost').closest('button')!
    expect(totalCostBtn).toHaveAttribute('aria-label', expect.stringContaining('not sorted'))

    // Verify DOM order is alphabetical ascending
    const agentCells = screen.getAllByText(/^Agent [A-Z]$/)
    const agentNames = agentCells.map((el) => el.textContent)
    expect(agentNames).toEqual(['Agent A', 'Agent B', 'Agent C'])
  })

  it('defaults to descending for numeric column (Tasks)', async () => {
    const user = userEvent.setup()
    render(<AgentSpendingTable rows={makeRows(3)} />)

    // Switch from totalCost (default) to Agent (text, asc) then to Tasks (numeric, desc)
    const agentBtn = screen.getByText('Agent').closest('button')!
    await user.click(agentBtn)
    expect(agentBtn).toHaveAttribute('aria-label', expect.stringContaining('sorted ascending'))

    const tasksBtn = screen.getByText('Tasks').closest('button')!
    await user.click(tasksBtn)
    expect(tasksBtn).toHaveAttribute('aria-label', expect.stringContaining('sorted descending'))
  })

  it('uses EUR currency by default', () => {
    const rows: AgentSpendingRow[] = [{
      agentId: 'a1',
      agentName: 'Alice',
      totalCost: 25.5,
      budgetPercent: 10,
      taskCount: 3,
      costPerTask: 8.5,
    }]
    render(<AgentSpendingTable rows={rows} />)
    // formatCurrency defaults to EUR
    expect(screen.getByText(formatCurrency(25.5))).toBeInTheDocument()
    expect(screen.getByText(formatCurrency(8.5))).toBeInTheDocument()
  })
})
