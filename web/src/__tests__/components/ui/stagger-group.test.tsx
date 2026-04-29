import { render, screen } from '@testing-library/react'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'

// Mock motion/react

vi.mock('motion/react', async () => {
  const actual = await vi.importActual<typeof import('motion/react')>('motion/react')
  return {
    ...actual,
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


describe('StaggerGroup', () => {
  it('renders all children', () => {
    render(
      <StaggerGroup>
        <StaggerItem>Card 1</StaggerItem>
        <StaggerItem>Card 2</StaggerItem>
        <StaggerItem>Card 3</StaggerItem>
      </StaggerGroup>,
    )
    expect(screen.getByText('Card 1')).toBeInTheDocument()
    expect(screen.getByText('Card 2')).toBeInTheDocument()
    expect(screen.getByText('Card 3')).toBeInTheDocument()
  })

  it('applies className to StaggerGroup wrapper', () => {
    const { container } = render(
      <StaggerGroup className="grid grid-cols-3">
        <StaggerItem>Card</StaggerItem>
      </StaggerGroup>,
    )
    expect(container.firstChild).toHaveClass('grid', 'grid-cols-3')
  })

  it('applies className to StaggerItem', () => {
    render(
      <StaggerGroup>
        <StaggerItem className="custom-item" data-testid="item">
          Card
        </StaggerItem>
      </StaggerGroup>,
    )
    expect(screen.getByTestId('item')).toHaveClass('custom-item')
  })

  it('renders correctly with no children', () => {
    const { container } = render(<StaggerGroup />)
    expect(container.firstChild).toBeInTheDocument()
  })
})
