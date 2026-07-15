import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { useProjectsStore } from '@/stores/projects'
import { makeProject } from '../helpers/factories'

const { roleRef } = vi.hoisted(() => {
  const ref: { current: string | null } = { current: 'ceo' }
  return { roleRef: ref }
})
vi.mock('@/stores/auth', () => ({ useUserRole: () => roleRef.current }))

import { ProjectOversightSection } from '@/pages/projects/ProjectOversightSection'

const setAutonomyMode = vi.fn()

function renderSection(project = makeProject('proj-001')) {
  return render(<ProjectOversightSection project={project} />)
}

describe('ProjectOversightSection', () => {
  beforeEach(() => {
    roleRef.current = 'ceo'
    setAutonomyMode.mockReset()
    setAutonomyMode.mockResolvedValue(null)
    useProjectsStore.setState({ setAutonomyMode, autonomyModeSaving: false })
  })

  function modeSelect(): HTMLSelectElement {
    return screen.getByLabelText<HTMLSelectElement>('Autonomy mode')
  }

  it('reflects the project current mode as the selected value', () => {
    renderSection(makeProject('proj-001', { autonomy_mode: 'supervised' }))
    expect(modeSelect().value).toBe('supervised')
  })

  it('defaults to Inherit when no override is set', () => {
    renderSection(makeProject('proj-001', { autonomy_mode: null }))
    expect(modeSelect().value).toBe('')
  })

  it('sets a non-full mode directly through the store', () => {
    renderSection()
    fireEvent.change(modeSelect(), { target: { value: 'locked' } })
    expect(setAutonomyMode).toHaveBeenCalledWith('proj-001', 'locked')
  })

  it('clears the override when Inherit is chosen', () => {
    renderSection(makeProject('proj-001', { autonomy_mode: 'semi' }))
    fireEvent.change(modeSelect(), { target: { value: '' } })
    expect(setAutonomyMode).toHaveBeenCalledWith('proj-001', null)
  })

  it('opens a confirmation for full and does not write until confirmed', async () => {
    renderSection()
    fireEvent.change(modeSelect(), { target: { value: 'full' } })
    expect(
      await screen.findByText('Turn off the security gate?'),
    ).toBeInTheDocument()
    expect(setAutonomyMode).not.toHaveBeenCalled()
  })

  it('sends confirm=true after the operator confirms the full opt-in', async () => {
    renderSection()
    fireEvent.change(modeSelect(), { target: { value: 'full' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Disable gate' }))
    await waitFor(() =>
      expect(setAutonomyMode).toHaveBeenCalledWith('proj-001', 'full', true),
    )
  })

  it('keeps the confirmation open when the full opt-in fails', async () => {
    setAutonomyMode.mockResolvedValue(null)
    renderSection()
    fireEvent.change(modeSelect(), { target: { value: 'full' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Disable gate' }))
    await waitFor(() =>
      expect(setAutonomyMode).toHaveBeenCalledWith('proj-001', 'full', true),
    )
    // The store returned its null failure sentinel: the dialog stays open so
    // the operator can retry from the same surface.
    expect(screen.getByText('Turn off the security gate?')).toBeInTheDocument()
  })

  it('closes the confirmation after a successful full opt-in', async () => {
    setAutonomyMode.mockResolvedValue(
      makeProject('proj-001', { autonomy_mode: 'full' }),
    )
    renderSection()
    fireEvent.change(modeSelect(), { target: { value: 'full' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Disable gate' }))
    await waitFor(() =>
      expect(
        screen.queryByText('Turn off the security gate?'),
      ).not.toBeInTheDocument(),
    )
  })

  it('disables the full option for a non-CEO role', () => {
    roleRef.current = 'manager'
    renderSection()
    const fullOption = screen.getByRole<HTMLOptionElement>('option', {
      name: /Unrestricted/,
    })
    expect(fullOption.disabled).toBe(true)
  })

  it('disables the control while a save is in flight', () => {
    useProjectsStore.setState({ autonomyModeSaving: true })
    renderSection()
    expect(modeSelect()).toBeDisabled()
  })
})
