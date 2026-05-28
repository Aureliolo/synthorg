import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EntityCard } from '@/pages/ontology/EntityCard'
import type { EntityResponse } from '@/api/endpoints/ontology'

function buildEntity(overrides: Partial<EntityResponse> = {}): EntityResponse {
  return {
    name: 'Task',
    tier: 'user',
    source: 'api',
    definition: 'A unit of work',
    fields: [],
    constraints: [],
    disambiguation: '',
    relationships: [],
    created_by: 'user-1',
    created_at: '2026-04-19T00:00:00Z',
    updated_at: '2026-04-19T00:00:00Z',
    ...overrides,
  }
}

describe('EntityCard', () => {
  it('invokes onClick when the card body is activated', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(<EntityCard entity={buildEntity()} onClick={onClick} />)

    await user.click(screen.getByRole('button', { name: /view entity: Task/i }))

    expect(onClick).toHaveBeenCalledOnce()
  })

  it('does not render an actions menu when onDelete is omitted', () => {
    render(<EntityCard entity={buildEntity()} />)

    expect(
      screen.queryByRole('button', { name: /entity actions/i }),
    ).not.toBeInTheDocument()
  })

  it('renders field and relationship counts', () => {
    render(
      <EntityCard
        entity={buildEntity({
          fields: [{ name: 'id', type_hint: 'str', description: '' }],
          relationships: [{ target: 'Agent', relation: 'owned_by', description: '' }],
        })}
      />,
    )

    expect(screen.getByText('1 fields')).toBeInTheDocument()
    expect(screen.getByText('1 relations')).toBeInTheDocument()
  })

  it('deletes through a confirmation dialog', async () => {
    const user = userEvent.setup()
    const onDelete = vi.fn()
    render(<EntityCard entity={buildEntity({ name: 'Task' })} onDelete={onDelete} />)

    await user.click(screen.getByRole('button', { name: /entity actions: Task/i }))
    await user.click(await screen.findByRole('menuitem', { name: /delete/i }))

    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('Delete entity')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: /^delete$/i }))

    expect(onDelete).toHaveBeenCalledWith('Task')
  })

  it('does not call onDelete when the dialog is cancelled', async () => {
    const user = userEvent.setup()
    const onDelete = vi.fn()
    render(<EntityCard entity={buildEntity({ name: 'Task' })} onDelete={onDelete} />)

    await user.click(screen.getByRole('button', { name: /entity actions: Task/i }))
    await user.click(await screen.findByRole('menuitem', { name: /delete/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /cancel/i }))

    expect(onDelete).not.toHaveBeenCalled()
  })
})
