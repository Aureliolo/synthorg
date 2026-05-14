import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AgentEditDrawer } from '@/pages/org-edit/AgentEditDrawer'
import { makeAgent, makeDepartment } from '../../helpers/factories'

describe('AgentEditDrawer', () => {
  const mockOnUpdate = vi.fn().mockResolvedValue(makeAgent('alice'))
  const mockOnDelete = vi.fn().mockResolvedValue(undefined)
  const mockOnClose = vi.fn()
  const agent = makeAgent('alice', { role: 'Lead Developer', level: 'lead' })
  const departments = [makeDepartment('engineering'), makeDepartment('product')]

  function renderDrawer(props?: { agent?: typeof agent | null; open?: boolean }) {
    return render(
      <AgentEditDrawer
        open={props?.open ?? true}
        onClose={mockOnClose}
        agent={props?.agent ?? agent}
        departments={departments}
        onUpdate={mockOnUpdate}
        onDelete={mockOnDelete}
        saving={false}
      />,
    )
  }

  beforeEach(() => vi.resetAllMocks())

  it('renders agent info when open', () => {
    renderDrawer()
    expect(screen.getByText(/Edit: alice/)).toBeInTheDocument()
    expect(screen.getByDisplayValue('alice')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Lead Developer')).toBeInTheDocument()
  })

  it('renders Delete button', () => {
    renderDrawer()
    expect(screen.getByText('Delete')).toBeInTheDocument()
  })

  it('shows model info as read-only', () => {
    renderDrawer()
    expect(screen.getByText(/test-provider/)).toBeInTheDocument()
    expect(screen.getByText(/test-medium-001/)).toBeInTheDocument()
  })

  it('renders Save and Delete buttons as enabled', () => {
    renderDrawer()
    const saveButton = screen.getByRole('button', { name: /save/i })
    const deleteButton = screen.getByRole('button', { name: /delete/i })
    expect(saveButton).not.toBeDisabled()
    expect(deleteButton).not.toBeDisabled()
  })

  it('calls onUpdate when Save is clicked', async () => {
    renderDrawer()
    fireEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(mockOnUpdate).toHaveBeenCalledTimes(1)
      expect(mockOnUpdate).toHaveBeenCalledWith('alice', {
        name: 'alice',
        role: 'Lead Developer',
        department: 'engineering',
        level: 'lead',
      })
    })
  })

  it('calls onDelete after confirming in ConfirmDialog', async () => {
    renderDrawer()

    // Click the Delete button in the drawer to open the ConfirmDialog
    fireEvent.click(screen.getByRole('button', { name: /delete/i }))

    // The ConfirmDialog should now be open with a destructive "Delete" confirm button.
    // Wait for all delete buttons to appear, then find the destructive one and
    // click it OUTSIDE waitFor to avoid firing multiple times on retries.
    const allDeleteButtons = await screen.findAllByRole('button', { name: /delete/i })
    const destructiveButton = allDeleteButtons.find(
      (btn) => btn.getAttribute('data-variant') === 'destructive',
    )
    expect(destructiveButton).toBeDefined()
    fireEvent.click(destructiveButton!)

    await waitFor(() => {
      expect(mockOnDelete).toHaveBeenCalledTimes(1)
      expect(mockOnDelete).toHaveBeenCalledWith('alice')
    })
  })
})
