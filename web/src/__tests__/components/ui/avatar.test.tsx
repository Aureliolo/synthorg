import { render, screen } from '@testing-library/react'
import * as fc from 'fast-check'
import { Avatar } from '@/components/ui/avatar'

describe('Avatar', () => {
  it('renders initials from a two-word name', () => {
    render(<Avatar name="Alice Smith" />)

    expect(screen.getByText('AS')).toBeInTheDocument()
  })

  it('renders single initial from a one-word name', () => {
    render(<Avatar name="Alice" />)

    expect(screen.getByText('A')).toBeInTheDocument()
  })

  it('uses first and last initials for three-word names', () => {
    render(<Avatar name="Alice Marie Smith" />)

    expect(screen.getByText('AS')).toBeInTheDocument()
  })

  it('has accessible aria-label with full name', () => {
    render(<Avatar name="Alice Smith" />)

    expect(screen.getByLabelText('Alice Smith')).toBeInTheDocument()
  })

  it('applies small size classes', () => {
    const { container } = render(<Avatar name="A" size="sm" />)

    expect(container.firstChild).toHaveClass('size-6')
  })

  it('applies medium size classes by default', () => {
    const { container } = render(<Avatar name="A" />)

    expect(container.firstChild).toHaveClass('size-8')
  })

  it('applies large size classes', () => {
    const { container } = render(<Avatar name="A" size="lg" />)

    expect(container.firstChild).toHaveClass('size-10')
  })

  it('handles empty name gracefully', () => {
    render(<Avatar name="" />)

    const avatar = screen.getByRole('img')
    expect(avatar).toBeInTheDocument()
    expect(avatar).not.toHaveAttribute('aria-label')
  })

  it('handles whitespace-only name gracefully', () => {
    render(<Avatar name="   " />)

    const avatar = screen.getByRole('img')
    expect(avatar).toBeInTheDocument()
    expect(avatar.textContent).toBe('')
  })

  it('renders without crashing for arbitrary names (property)', () => {
    fc.assert(
      fc.property(fc.string(), (name) => {
        const { unmount } = render(<Avatar name={name} />)
        unmount()
      }),
    )
  })

  it('initials are at most 2 characters (property)', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1 }), (name) => {
        const { container, unmount } = render(<Avatar name={name} />)
        const text = container.textContent
        expect(text.length).toBeLessThanOrEqual(2)
        unmount()
      }),
    )
  })
})
