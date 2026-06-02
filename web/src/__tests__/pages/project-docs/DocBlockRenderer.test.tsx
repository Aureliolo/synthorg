import { render, screen } from '@testing-library/react'
import { DocBlockRenderer } from '@/pages/project-docs/DocBlockRenderer'
import type { LinkBlock } from '@/api/types'

function linkBlock(url: string): LinkBlock {
  return { block_id: 'b1', block_kind: 'link', label: 'Source', url }
}

describe('DocBlockRenderer link sanitization', () => {
  it('passes https urls through unchanged', () => {
    render(<DocBlockRenderer block={linkBlock('https://example.com/x')} />)

    expect(screen.getByRole('link')).toHaveAttribute(
      'href',
      'https://example.com/x',
    )
  })

  it('coerces javascript: urls to a non-navigable anchor', () => {
    render(<DocBlockRenderer block={linkBlock('javascript:alert(1)')} />)

    expect(screen.getByRole('link')).toHaveAttribute('href', '#')
  })

  it('coerces protocol-relative urls to a non-navigable anchor', () => {
    render(<DocBlockRenderer block={linkBlock('//evil.example.com')} />)

    expect(screen.getByRole('link')).toHaveAttribute('href', '#')
  })

  it('passes internal brain-entry anchors through', () => {
    render(<DocBlockRenderer block={linkBlock('#brain-entry-dec-1')} />)

    expect(screen.getByRole('link')).toHaveAttribute(
      'href',
      '#brain-entry-dec-1',
    )
  })

  it('coerces protocol-relative urls hidden behind leading whitespace', () => {
    // Browsers trim leading whitespace from href, so " //evil" would
    // resolve as an open-redirect if the prefix check ran untrimmed.
    render(<DocBlockRenderer block={linkBlock(' //evil.example.com')} />)

    expect(screen.getByRole('link')).toHaveAttribute('href', '#')
  })

  it('coerces whitespace-prefixed disallowed-scheme urls', () => {
    render(<DocBlockRenderer block={linkBlock(' javascript:alert(1)')} />)

    expect(screen.getByRole('link')).toHaveAttribute('href', '#')
  })
})
