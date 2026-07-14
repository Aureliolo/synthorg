import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AgentCreateDialog } from '@/pages/org-edit/AgentCreateDialog'
import { makeDepartment } from '../../helpers/factories'

describe('AgentCreateDialog', () => {
  const mockOnCreate = vi.fn().mockResolvedValue({ id: 'new-agent', name: 'test' })
  const mockOnOpenChange = vi.fn()
  const departments = [makeDepartment('engineering'), makeDepartment('product')]

  function renderDialog(open = true) {
    return render(
      <AgentCreateDialog
        open={open}
        onOpenChange={mockOnOpenChange}
        departments={departments}
        onCreate={mockOnCreate}
      />,
    )
  }

  beforeEach(() => vi.resetAllMocks())

  it('renders form fields when open', () => {
    renderDialog()
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/role/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/department/i)).toBeInTheDocument()
  })

  it('renders Create Agent button as enabled', () => {
    renderDialog()
    const createButton = screen.getByRole('button', { name: /create agent/i })
    expect(createButton).not.toBeDisabled()
  })

  it('does not render when closed', () => {
    renderDialog(false)
    expect(screen.queryByText('New Agent')).not.toBeInTheDocument()
  })

  it('submits trimmed payload on Create Agent click', async () => {
    renderDialog()

    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: '  Alice  ' } })
    fireEvent.change(screen.getByLabelText(/role/i), { target: { value: '  Backend Dev  ' } })
    fireEvent.change(screen.getByLabelText(/department/i), { target: { value: 'engineering' } })
    fireEvent.click(screen.getByRole('button', { name: /create agent/i }))

    await waitFor(() => {
      expect(mockOnCreate).toHaveBeenCalledTimes(1)
      expect(mockOnCreate).toHaveBeenCalledWith({
        name: 'Alice',
        role: 'Backend Dev',
        department: 'engineering',
      })
    })
  })
})
