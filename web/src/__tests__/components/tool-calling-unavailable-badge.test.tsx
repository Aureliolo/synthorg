import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ToolCallingUnavailableBadge } from '@/components/ui/tool-calling-unavailable-badge'

describe('ToolCallingUnavailableBadge', () => {
  it('renders the badge when tool calling is runtime-unavailable', () => {
    render(<ToolCallingUnavailableBadge toolCallsVerified={false} />)
    expect(screen.getByText('No tool calling')).toBeInTheDocument()
  })

  it('renders nothing when proven', () => {
    const { container } = render(<ToolCallingUnavailableBadge toolCallsVerified={true} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when unobserved (null)', () => {
    const { container } = render(<ToolCallingUnavailableBadge toolCallsVerified={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when undefined', () => {
    const { container } = render(
      <ToolCallingUnavailableBadge toolCallsVerified={undefined} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
