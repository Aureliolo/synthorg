import { render, screen } from '@testing-library/react'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { motionReactMockFactory } from '@/test-utils/mock-motion'

// Shared motion/react mock: AnimatePresence is identity, motion.div drops
// animation props and forwards everything else.
vi.mock('motion/react', async () => {
  const actual = await vi.importActual<typeof import('motion/react')>('motion/react')
  return { ...actual, ...motionReactMockFactory() }
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
