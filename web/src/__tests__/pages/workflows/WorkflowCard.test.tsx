import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowCard } from '@/pages/workflows/WorkflowCard'
import { buildWorkflow } from '@/mocks/handlers/workflows'

function renderCard() {
  const onDelete = vi.fn()
  const onDuplicate = vi.fn()
  const onExport = vi.fn()
  render(
    <MemoryRouter>
      <WorkflowCard
        workflow={buildWorkflow({ id: 'wf-1', name: 'My Workflow' })}
        onDelete={onDelete}
        onDuplicate={onDuplicate}
        onExport={onExport}
      />
    </MemoryRouter>,
  )
  return { onDelete, onDuplicate, onExport }
}

describe('WorkflowCard', () => {
  it('exports YAML from the actions menu', async () => {
    const user = userEvent.setup()
    const { onExport } = renderCard()

    await user.click(screen.getByRole('button', { name: /workflow actions/i }))
    await user.click(await screen.findByRole('menuitem', { name: /export yaml/i }))

    expect(onExport).toHaveBeenCalledWith('wf-1')
  })

  it('guards delete behind a confirmation dialog', async () => {
    const user = userEvent.setup()
    const { onDelete } = renderCard()

    await user.click(screen.getByRole('button', { name: /workflow actions/i }))
    await user.click(await screen.findByRole('menuitem', { name: /^delete$/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^delete$/i }))

    expect(onDelete).toHaveBeenCalledWith('wf-1')
  })
})
