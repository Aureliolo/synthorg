import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProviderLogo } from '../ProviderLogo'

describe('ProviderLogo', () => {
  it('renders the bundled mask-image element for a known preset', () => {
    const { container } = render(<ProviderLogo name="anthropic" />)
    const span = container.querySelector('[data-provider-logo="anthropic"]') as HTMLElement | null
    expect(span).not.toBeNull()
    expect(span?.style.maskImage).toContain('/provider-logos/anthropic.svg')
    // No <img> tag is used: brand colour is applied via background +
    // mask-image so the dashboard's text-secondary token drives it
    // across themes.
    expect(container.querySelector('img')).toBeNull()
  })

  it('falls back to the Server icon for an unknown preset name', () => {
    const { container } = render(<ProviderLogo name="missing-vendor" />)
    expect(container.querySelector('[data-provider-logo]')).toBeNull()
    // lucide-react renders an SVG element; verify the fallback path.
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('respects the size prop on both bundled and fallback variants', () => {
    const known = render(<ProviderLogo name="openai" size={48} />)
    const span = known.container.querySelector('[data-provider-logo="openai"]') as HTMLElement | null
    expect(span?.style.width).toBe('48px')
    expect(span?.style.height).toBe('48px')

    const fallback = render(<ProviderLogo name="bogus" size={48} />)
    const svg = fallback.container.querySelector('svg')
    expect(svg?.style.width).toBe('48px')
    expect(svg?.style.height).toBe('48px')
  })
})
