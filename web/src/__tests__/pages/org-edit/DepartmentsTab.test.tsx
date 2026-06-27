import { render, screen } from '@testing-library/react'
import { DepartmentsTab, type DepartmentsTabProps } from '@/pages/org-edit/DepartmentsTab'
import { makeCompanyConfig, makeDepartmentHealth } from '../../helpers/factories'

const noopAsync = vi.fn().mockResolvedValue(undefined)
const noopRollback = vi.fn().mockReturnValue(() => {})

function renderTab(overrides?: Partial<DepartmentsTabProps>) {
  const props: DepartmentsTabProps = {
    config: makeCompanyConfig(),
    departmentHealths: [
      makeDepartmentHealth('engineering'),
      makeDepartmentHealth('product', { utilization_percent: 72, agent_count: 1 }),
    ],
    saving: false,
    onCreateDepartment: noopAsync,
    onUpdateDepartment: noopAsync,
    onDeleteDepartment: noopAsync,
    onReorderDepartments: noopAsync,
    optimisticReorderDepartments: noopRollback,
    onCreateTeam: noopAsync,
    onUpdateTeam: noopAsync,
    onDeleteTeam: noopAsync,
    onReorderTeams: noopAsync,
    ...overrides,
  }
  return render(<DepartmentsTab {...props} />)
}

describe('DepartmentsTab', () => {
  beforeEach(() => vi.resetAllMocks())

  it('renders empty state when config has no departments', () => {
    renderTab({ config: { ...makeCompanyConfig(), departments: [] } })
    expect(screen.getByText('No departments')).toBeInTheDocument()
  })

  it('renders Add Department button', () => {
    renderTab()
    expect(screen.getByText('Add Department')).toBeInTheDocument()
  })

  it('renders department cards', () => {
    renderTab()
    // Department cards in this editor view show only edit-time metadata
    // (agent count, team count, optional budget percent); runtime health is
    // surfaced through the status bar, not here.
    expect(screen.getAllByText('Engineering').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Product').length).toBeGreaterThanOrEqual(1)
  })

  it('does not render runtime utilization meters on the editor surface', () => {
    renderTab()
    // Editor cards intentionally omit the live utilization gauge so
    // the page stays focused on configuration -- the live health view
    // lives on the Dashboard and Org Chart pages.
    expect(screen.queryAllByRole('meter')).toHaveLength(0)
  })

  it('renders empty state when config is null', () => {
    renderTab({ config: null })
    expect(screen.getByText('No departments')).toBeInTheDocument()
  })
})
