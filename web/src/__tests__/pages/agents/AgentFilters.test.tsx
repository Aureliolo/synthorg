import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AgentFilters } from '@/pages/agents/AgentFilters'
import { useAgentsStore } from '@/stores/agents'
import { useCompanyStore } from '@/stores/company'
import { makeCompanyConfig } from '../../helpers/factories'

const mockSetSearchQuery = vi.fn()
const mockSetDepartmentFilter = vi.fn()
const mockSetStatusFilter = vi.fn()
const mockSetSortBy = vi.fn()

function resetStore() {
  useAgentsStore.setState({
    searchQuery: '',
    departmentFilter: null,
    statusFilter: null,
    sortBy: 'name',
    sortDirection: 'asc',
    setSearchQuery: mockSetSearchQuery,
    setDepartmentFilter: mockSetDepartmentFilter,
    setStatusFilter: mockSetStatusFilter,
    setSortBy: mockSetSortBy,
  })
  // The filter's department dropdown now pulls from the live
  // company config, so tests that exercise the dropdown need to
  // seed the company store with at least one department.
  useCompanyStore.setState({
    config: makeCompanyConfig(),
  })
}

describe('AgentFilters', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetStore()
  })

  it('renders search input', () => {
    render(<AgentFilters />)
    expect(screen.getByLabelText('Search agents')).toBeInTheDocument()
  })

  it('renders filter dropdowns', () => {
    render(<AgentFilters />)
    expect(screen.getByLabelText('Filter by department')).toBeInTheDocument()
    expect(screen.getByLabelText('Filter by status')).toBeInTheDocument()
    expect(screen.getByLabelText('Sort agents by')).toBeInTheDocument()
  })

  it('calls setSearchQuery on search input change', async () => {
    const user = userEvent.setup()
    render(<AgentFilters />)
    await user.type(screen.getByLabelText('Search agents'), 'a')
    expect(mockSetSearchQuery).toHaveBeenCalledWith('a')
  })

  it('maps empty string to null for department filter', async () => {
    const user = userEvent.setup()
    render(<AgentFilters />)
    await user.selectOptions(screen.getByLabelText('Filter by department'), 'engineering')
    expect(mockSetDepartmentFilter).toHaveBeenCalledWith('engineering')
    mockSetDepartmentFilter.mockClear()
    await user.selectOptions(screen.getByLabelText('Filter by department'), '')
    expect(mockSetDepartmentFilter).toHaveBeenCalledWith(null)
  })

  it('calls setSortBy on sort select change', async () => {
    const user = userEvent.setup()
    render(<AgentFilters />)
    await user.selectOptions(screen.getByLabelText('Sort agents by'), 'department')
    expect(mockSetSortBy).toHaveBeenCalledWith('department')
  })
})
