import { render, screen } from '@testing-library/react'
import { OrgHealthSection } from '@/pages/dashboard/OrgHealthSection'
import { formatCurrency } from '@/utils/format'
import type { DepartmentHealth } from '@/api/types/analytics'

function makeDepts(count: number): DepartmentHealth[] {
  const names = ['engineering', 'design', 'product', 'operations', 'security'] as const
  return Array.from({ length: count }, (_, i) => {
    const name = names[i % names.length]!
    return {
      department_name: name,
      agent_count: 2 + i,
      active_agent_count: 1 + i,
      currency: 'EUR',
      avg_performance_score: 7.0,
      department_cost_7d: 0,
      cost_trend: [],
      collaboration_score: 6.0,
      total_runs: 10,
      task_success_rate: (60 + i * 10) / 100,
      utilization_percent: 60 + i * 10,
      utilization_degraded: false,
      health_score: 60 + i * 10,
    }
  })
}

/** A no-data department: fully staffed/utilised but no runs to judge. */
function makeNoDataDept(): DepartmentHealth {
  return {
    department_name: 'engineering',
    agent_count: 4,
    active_agent_count: 4,
    currency: 'EUR',
    avg_performance_score: null,
    department_cost_7d: 0,
    cost_trend: [],
    collaboration_score: null,
    total_runs: 0,
    task_success_rate: null,
    utilization_percent: 100,
    utilization_degraded: false,
    health_score: null,
  }
}

/**
 * Render the panel, defaulting the department count to what reported health.
 *
 * The two differ only when a health read failed, which is its own pair of
 * cases below; everywhere else the count follows the list.
 */
function renderSection(
  departments: readonly DepartmentHealth[],
  overallHealth: number | null,
  departmentCount = departments.length,
) {
  return render(
    <OrgHealthSection
      departments={departments}
      departmentCount={departmentCount}
      overallHealth={overallHealth}
    />,
  )
}

describe('OrgHealthSection', () => {
  it('renders section title', () => {
    renderSection([], null)
    expect(screen.getByText('Org Health')).toBeInTheDocument()
  })

  it('shows empty state when no departments', () => {
    renderSection([], null)
    expect(screen.getByText('No departments configured')).toBeInTheDocument()
  })

  it('does not report an org with departments as unconfigured', () => {
    // The health calls were refused; the list call was not. Telling an
    // operator with six departments and twelve agents to go and set their
    // organisation up is the one reading the data rules out.
    renderSection([], null, 6)

    expect(screen.getByText('Health metrics unavailable')).toBeInTheDocument()
    expect(screen.queryByText('No departments configured')).not.toBeInTheDocument()
  })

  it('says how many departments are configured when health is unavailable', () => {
    renderSection([], null, 6)
    expect(screen.getByText(/6 departments are configured/)).toBeInTheDocument()
  })

  it('renders department health bars', () => {
    renderSection(makeDepts(3), 70)
    expect(screen.getByText('Engineering')).toBeInTheDocument()
    expect(screen.getByText('Design')).toBeInTheDocument()
    expect(screen.getByText('Product')).toBeInTheDocument()
  })

  it('pluralises the run-count label (singular for one run)', () => {
    const [dept] = makeDepts(1)
    renderSection([{ ...dept!, total_runs: 1 }], 70)
    // "· 1 run", never "· 1 runs".
    expect(screen.getByText(/·\s*1 run\b/)).toBeInTheDocument()
  })

  it('hides the run-count label when there are no runs', () => {
    renderSection([makeNoDataDept()], null)
    // The count label ("· N run" / "· N runs") must be absent entirely; the
    // pattern spans the separator + count + singular-or-plural so a "· 0 runs"
    // regression is caught, not silently passed by a singular-only match.
    expect(screen.queryByText(/·\s*\d+\s+runs?\b/)).not.toBeInTheDocument()
  })

  it('renders overall health gauge when provided', () => {
    renderSection(makeDepts(1), 85)
    const meters = screen.getAllByRole('meter')
    expect(meters.length).toBeGreaterThanOrEqual(1)
  })

  it('renders department cost when department_cost_7d is positive', () => {
    renderSection(
      makeDepts(1).map((d) => ({ ...d, department_cost_7d: 24.5 })),
      80,
    )
    expect(screen.getByText(formatCurrency(24.5, 'EUR'))).toBeInTheDocument()
  })

  it('renders department cost in its own currency', () => {
    renderSection(
      makeDepts(1).map((d) => ({ ...d, department_cost_7d: 100, currency: 'JPY' })),
      80,
    )
    expect(screen.getByText(formatCurrency(100, 'JPY'))).toBeInTheDocument()
  })

  it('shows an explicit no-data state instead of a gauge when overall is null', () => {
    renderSection([makeNoDataDept()], null)
    expect(screen.getByText(/awaiting task activity/i)).toBeInTheDocument()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })

  it('renders a no-data department bar as N/A, not full health', () => {
    renderSection([makeNoDataDept()], null)
    expect(screen.getByText('N/A')).toBeInTheDocument()
  })
})
