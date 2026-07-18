import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ChatBubble } from '../chat-bubble'

describe('ChatBubble', () => {
  it('labels a human turn textually, not by colour alone', () => {
    render(<ChatBubble variant="human" content="How much runway?" />)
    expect(screen.getByText('You')).toBeInTheDocument()
    expect(screen.getByText('How much runway?')).toBeInTheDocument()
  })

  it('renders an assistant turn as markdown with a copy affordance', () => {
    const { container } = render(
      <ChatBubble variant="assistant" content="Runway is **7 months**." />,
    )
    expect(screen.getByText('Chief of Staff')).toBeInTheDocument()
    expect(container.querySelector('strong')?.textContent).toBe('7 months')
    expect(
      screen.getByRole('button', { name: 'Copy message' }),
    ).toBeInTheDocument()
  })

  it('attributes an agent turn to its name and role', () => {
    render(
      <ChatBubble
        variant="agent"
        agentName="Casey"
        agentRole="CFO"
        agentTopic="budget"
        content="About seven months."
      />,
    )
    expect(screen.getByText('Casey')).toBeInTheDocument()
    expect(screen.getByText('CFO')).toBeInTheDocument()
  })

  it('renders an error notice with its message and no copy button', () => {
    render(
      <ChatBubble variant="notice" isError>
        That turn failed.
      </ChatBubble>,
    )
    expect(screen.getByText('That turn failed.')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Copy message' }),
    ).not.toBeInTheDocument()
  })
})
