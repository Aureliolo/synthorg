import { render, screen } from '@testing-library/react'
import type { SettingSource } from '@/api/types/settings'
import { SourceBadge } from '@/pages/settings/SourceBadge'

describe('SourceBadge', () => {
  it('renders "Modified" for db source', () => {
    render(<SourceBadge source="db" />)
    expect(screen.getByText('Modified')).toBeInTheDocument()
  })

  it('renders "ENV" for env source', () => {
    render(<SourceBadge source="env" />)
    expect(screen.getByText('ENV')).toBeInTheDocument()
  })

  it('renders null for default source', () => {
    const { container } = render(<SourceBadge source="default" />)
    expect(container.firstChild).toBeNull()
  })

  it('applies accent styling for db source', () => {
    render(<SourceBadge source="db" />)
    const badge = screen.getByText('Modified')
    expect(badge.className).toContain('text-accent')
  })

  it('applies warning styling for env source', () => {
    render(<SourceBadge source="env" />)
    const badge = screen.getByText('ENV')
    expect(badge.className).toContain('text-warning')
  })

  it('accepts custom className', () => {
    render(<SourceBadge source="db" className="ml-2" />)
    const badge = screen.getByText('Modified')
    expect(badge.className).toContain('ml-2')
  })

  it('covers all SettingSource values', () => {
    // Exhaustive check that every SettingSource is handled
    const sources: SettingSource[] = ['db', 'env', 'default']
    for (const source of sources) {
      const { unmount } = render(<SourceBadge source={source} />)
      unmount()
    }
  })
})
