import { cleanup, render, renderHook, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { CommandItem } from '@/hooks/useCommandPalette'
import {
  _reset,
  _setOpen,
  registerCommands,
  useRegisterCommands,
} from '@/hooks/useCommandPalette'
import { CommandPalette } from '@/components/ui/command-palette'

let originalResizeObserver: typeof globalThis.ResizeObserver
let originalScrollIntoView: typeof Element.prototype.scrollIntoView

beforeAll(() => {
  originalResizeObserver = globalThis.ResizeObserver
  // eslint-disable-next-line @typescript-eslint/unbound-method -- saved to restore the original prototype method after the suite; never invoked unbound
  originalScrollIntoView = Element.prototype.scrollIntoView

  // cmdk uses ResizeObserver and scrollIntoView which are not in jsdom.
  // Implementing the ``ResizeObserver`` interface makes the assignment
  // structurally type-safe -- no cast needed.
  class ResizeObserverMock implements ResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver = ResizeObserverMock
  Element.prototype.scrollIntoView = vi.fn()
})

afterAll(() => {
  globalThis.ResizeObserver = originalResizeObserver
  Element.prototype.scrollIntoView = originalScrollIntoView
})

function makeCommand(overrides: Partial<CommandItem> = {}): CommandItem {
  return {
    id: `cmd-${Math.random().toString(36).slice(2)}`,
    label: 'Dashboard',
    action: vi.fn(),
    group: 'Navigation',
    ...overrides,
  }
}

let cleanupCommands: (() => void) | null = null

function setupCommands(commands: CommandItem[]) {
  cleanupCommands?.()
  cleanupCommands = registerCommands(commands)
}

describe('CommandPalette', () => {
  beforeEach(() => {
    cleanupCommands?.()
    cleanupCommands = null
    _reset()
    localStorage.clear()
  })

  it('is not rendered when closed', () => {
    render(<CommandPalette />)
    expect(screen.queryByText('Search commands...')).not.toBeInTheDocument()
  })

  it('opens on Ctrl+K', async () => {
    const user = userEvent.setup()
    render(<CommandPalette />)

    await user.keyboard('{Control>}k{/Control}')
    expect(screen.getByPlaceholderText('Search commands...')).toBeInTheDocument()
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    _setOpen(true)
    render(<CommandPalette />)

    expect(screen.getByPlaceholderText('Search commands...')).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByPlaceholderText('Search commands...')).not.toBeInTheDocument()
  })

  it('closes on Ctrl+K again', async () => {
    const user = userEvent.setup()
    render(<CommandPalette />)

    await user.keyboard('{Control>}k{/Control}')
    expect(screen.getByPlaceholderText('Search commands...')).toBeInTheDocument()

    await user.keyboard('{Control>}k{/Control}')
    expect(screen.queryByPlaceholderText('Search commands...')).not.toBeInTheDocument()
  })

  it('displays registered commands', () => {
    setupCommands([
      makeCommand({ label: 'Dashboard', group: 'Navigation' }),
      makeCommand({ label: 'Settings', group: 'Navigation' }),
    ])
    _setOpen(true)
    render(<CommandPalette />)

    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.getByText('Settings')).toBeInTheDocument()
  })

  it('shows "No results found" for unmatched search', async () => {
    const user = userEvent.setup()
    setupCommands([makeCommand({ label: 'Dashboard' })])
    _setOpen(true)
    render(<CommandPalette />)

    await user.type(screen.getByPlaceholderText('Search commands...'), 'zzzzzzz')
    expect(screen.getByText('No results found.')).toBeInTheDocument()
  })

  it('selecting a command calls its action', async () => {
    const user = userEvent.setup()
    const action = vi.fn()
    setupCommands([makeCommand({ label: 'Dashboard', action })])
    _setOpen(true)
    render(<CommandPalette />)

    await user.click(screen.getByText('Dashboard'))
    expect(action).toHaveBeenCalledTimes(1)
  })

  it('useRegisterCommands registers commands on mount and cleans up on unmount', () => {
    const commands = [makeCommand({ label: 'Mounted Cmd' })]
    const { unmount: unmountHook } = renderHook(() => useRegisterCommands(commands))

    // Commands should be registered
    _setOpen(true)
    const palette = render(<CommandPalette />)
    expect(screen.getByText('Mounted Cmd')).toBeInTheDocument()
    palette.unmount()

    // Unmount hook should clean up commands
    unmountHook()
    _setOpen(true)
    render(<CommandPalette />)
    expect(screen.queryByText('Mounted Cmd')).not.toBeInTheDocument()
    cleanup()
  })
})
