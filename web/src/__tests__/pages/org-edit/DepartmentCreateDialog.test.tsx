import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { DepartmentCreateDialog } from '@/pages/org-edit/DepartmentCreateDialog'

describe('DepartmentCreateDialog', () => {
  const mockOnOpenChange = vi.fn()
  const mockOnCreate = vi.fn().mockResolvedValue({ name: 'test', display_name: 'Test' })

  function renderDialog(open = true) {
    return render(
      <DepartmentCreateDialog
        open={open}
        onOpenChange={mockOnOpenChange}
        onCreate={mockOnCreate}
      />,
    )
  }

  beforeEach(() => vi.resetAllMocks())

  it('renders form fields when open', () => {
    renderDialog()
    expect(screen.getByText('New Department')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('e.g. engineering')).toBeInTheDocument()
  })

  it('renders Create Department button as enabled', () => {
    renderDialog()
    const createButton = screen.getByRole('button', { name: /create department/i })
    expect(createButton).not.toBeDisabled()
  })

  it('submits payload on Create Department click', async () => {
    renderDialog()

    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: '  design  ' } })
    fireEvent.change(screen.getByLabelText(/budget/i), { target: { value: '25' } })

    fireEvent.click(screen.getByRole('button', { name: /create department/i }))

    await waitFor(() => {
      expect(mockOnCreate).toHaveBeenCalledTimes(1)
      expect(mockOnCreate).toHaveBeenCalledWith({
        name: 'design',
        budget_percent: 25,
      })
    })
  })
})
