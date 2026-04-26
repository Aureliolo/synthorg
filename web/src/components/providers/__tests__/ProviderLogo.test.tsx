import { render, fireEvent } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProviderLogo } from '../ProviderLogo'

describe('ProviderLogo', () => {
  it('renders the bundled SVG by default', () => {
    const { container } = render(<ProviderLogo name="anthropic" />)
    const img = container.querySelector('img')
    expect(img?.getAttribute('src')).toContain('/provider-logos/anthropic.svg')
    expect(img).toHaveAttribute('aria-hidden', 'true')
  })

  it('falls back to the Server icon when the SVG fails to load', () => {
    const { container } = render(<ProviderLogo name="missing-vendor" />)
    const img = container.querySelector('img')
    expect(img).not.toBeNull()
    if (img) fireEvent.error(img)
    // After error, the lucide Server SVG renders in place; the <img>
    // is removed.
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('respects the size prop on both image and fallback', () => {
    const { container } = render(<ProviderLogo name="size-test" size={48} />)
    const img = container.querySelector('img')
    expect(img?.style.width).toBe('48px')
    expect(img?.style.height).toBe('48px')

    if (img) fireEvent.error(img)
    const svg = container.querySelector('svg')
    expect(svg?.style.width).toBe('48px')
    expect(svg?.style.height).toBe('48px')
  })
})
