import { render, screen } from '@testing-library/react'
import { AnimatedPresence } from '@/components/ui/animated-presence'

// Mock motion/react to avoid animation timing issues in tests

vi.mock('motion/react', async () => {
  const actual = await vi.importActual<typeof import('motion/react')>('motion/react')
  return {
    ...actual,
    useReducedMotion: () => false,
    AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    motion: {
      ...actual.motion,
      div: ({
        children,
        className,
        'data-testid': testId,
        ...rest
      }: React.HTMLAttributes<HTMLDivElement> & { 'data-testid'?: string }) => (
        <div className={className} data-testid={testId} {...rest}>
          {children}
        </div>
      ),
    },
  }
})


describe('AnimatedPresence', () => {
  it('renders children', () => {
    render(
      <AnimatedPresence routeKey="/">
        <div>Page content</div>
      </AnimatedPresence>,
    )
    expect(screen.getByText('Page content')).toBeInTheDocument()
  })

  it('applies className to wrapper', () => {
    const { container } = render(
      <AnimatedPresence routeKey="/" className="custom-class">
        <div>Content</div>
      </AnimatedPresence>,
    )
    expect(container.firstChild).toHaveClass('custom-class')
  })

  it('renders different content for different routeKeys', () => {
    const { rerender } = render(
      <AnimatedPresence routeKey="/page-a">
        <div>Page A</div>
      </AnimatedPresence>,
    )
    expect(screen.getByText('Page A')).toBeInTheDocument()

    rerender(
      <AnimatedPresence routeKey="/page-b">
        <div>Page B</div>
      </AnimatedPresence>,
    )
    expect(screen.getByText('Page B')).toBeInTheDocument()
  })
})
