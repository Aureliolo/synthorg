import { screen, waitFor } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ThemeStep } from '@/pages/setup/ThemeStep'
import { renderWithRouter } from '@/__tests__/test-utils'
import {
  ANIMATION_PRESETS,
  COLOR_PALETTES,
  DENSITIES,
  SIDEBAR_MODES,
} from '@/stores/theme'

/** Values of every radio in one option group, in render order. */
function radioValues(group: string): string[] {
  return Array.from(
    document.querySelectorAll<HTMLInputElement>(
      `input[type="radio"][name="${group}"]`,
    ),
  ).map((input) => input.value)
}

describe('ThemeStep', () => {
  it('offers every mode the store and backend accept', async () => {
    renderWithRouter(<ThemeStep />)
    await waitFor(() => expect(screen.getByText('Sidebar')).toBeInTheDocument())

    // The wizard previously hardcoded its own option lists, which drifted:
    // `persistent` and `aggressive` were selectable everywhere else in the
    // dashboard but simply absent here. Deriving from the store's exported
    // tuples makes that a type error; this asserts the rendered result.
    expect(radioValues('sidebar')).toEqual([...SIDEBAR_MODES])
    expect(radioValues('animation')).toEqual([...ANIMATION_PRESETS])
    expect(radioValues('palette')).toEqual([...COLOR_PALETTES])
    expect(radioValues('density')).toEqual([...DENSITIES])
  })

  it('describes rail as the icon-only mode, not the widest one', async () => {
    renderWithRouter(<ThemeStep />)
    await waitFor(() => expect(screen.getByText('Rail')).toBeInTheDocument())

    // Rail pins the nav collapsed at 56px. It was described as "icons and
    // labels (220px)", which is what `persistent` does.
    const rail = screen.getByText('Rail').parentElement
    expect(rail?.textContent).toContain('56px')
    expect(rail?.textContent).not.toContain('220px')

    const persistent = screen.getByText('Persistent').parentElement
    expect(persistent?.textContent).toContain('220px')
  })

  it('describes compact by its own width', async () => {
    renderWithRouter(<ThemeStep />)
    await waitFor(() => expect(screen.getByText('Compact')).toBeInTheDocument())

    // Compact is 180px; the 56px it used to claim belongs to rail.
    const compact = screen.getByText('Compact').parentElement
    expect(compact?.textContent).toContain('180px')
    expect(compact?.textContent).not.toContain('56px')
  })
})
