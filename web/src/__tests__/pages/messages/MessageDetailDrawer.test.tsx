import { render, screen } from '@testing-library/react'
import { MessageDetailDrawer } from '@/pages/messages/MessageDetailDrawer'
import { makeMessage } from '../../helpers/factories'
import type { Message } from '@/api/types/messages'

describe('MessageDetailDrawer', () => {
  const fullMessage: Message = makeMessage('msg-1', {
    sender: 'alice_chen',
    to: '#engineering',
    type: 'task_update',
    priority: 'high',
    text: 'PR ready for review.',
    parts: [
      { type: 'text', text: 'PR ready for review.' },
      { type: 'data', data: { ref: 'pr-42' } },
    ],
    metadata: {
      task_id: 'task-123',
      project_id: 'proj-456',
      tokens_used: 1200,
      cost: 0.018,
      extra: [['model', 'test-medium-001']],
    },
  })

  it('renders sender name and avatar', () => {
    render(<MessageDetailDrawer message={fullMessage} open={true} onClose={vi.fn()} />)
    // Sender name appears in both drawer title and content -- verify at least one exists
    expect(screen.getAllByText('alice_chen').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('img', { name: 'alice_chen' })).toBeInTheDocument()
  })

  it('renders message content', () => {
    render(<MessageDetailDrawer message={fullMessage} open={true} onClose={vi.fn()} />)
    expect(screen.getByText('PR ready for review.')).toBeInTheDocument()
  })

  it('renders metadata rows when present', () => {
    render(<MessageDetailDrawer message={fullMessage} open={true} onClose={vi.fn()} />)
    expect(screen.getByText('task-123')).toBeInTheDocument()
    expect(screen.getByText('proj-456')).toBeInTheDocument()
    expect(screen.getByText('1200')).toBeInTheDocument()
  })

  it('renders extra metadata key-value pairs', () => {
    render(<MessageDetailDrawer message={fullMessage} open={true} onClose={vi.fn()} />)
    expect(screen.getByText('model')).toBeInTheDocument()
    expect(screen.getByText('test-medium-001')).toBeInTheDocument()
  })

  it('renders attachments', () => {
    render(<MessageDetailDrawer message={fullMessage} open={true} onClose={vi.fn()} />)
    expect(screen.getByText('pr-42')).toBeInTheDocument()
  })

  it('hides optional metadata when null', () => {
    const minimal = makeMessage('msg-2', {
      sender: 'bob',
      metadata: { task_id: null, project_id: null, tokens_used: null, cost: null, extra: [] },
    })
    render(<MessageDetailDrawer message={minimal} open={true} onClose={vi.fn()} />)
    expect(screen.queryByText('Task ID')).not.toBeInTheDocument()
    expect(screen.queryByText('Project ID')).not.toBeInTheDocument()
    expect(screen.queryByText('Tokens')).not.toBeInTheDocument()
    expect(screen.queryByText('Cost')).not.toBeInTheDocument()
  })

  it('hides attachments section when no attachments', () => {
    const noAttachments = makeMessage('msg-3', {
      parts: [{ type: 'text', text: 'no attachments here' }],
    })
    render(<MessageDetailDrawer message={noAttachments} open={true} onClose={vi.fn()} />)
    expect(screen.queryByText('Attachments')).not.toBeInTheDocument()
  })

  it('renders nothing inside drawer when message is null', () => {
    render(<MessageDetailDrawer message={null} open={true} onClose={vi.fn()} />)
    // Drawer title falls back to 'Message'
    expect(screen.getByText('Message')).toBeInTheDocument()
    // No sender avatar should be present
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('renders priority badge for high priority', () => {
    render(<MessageDetailDrawer message={fullMessage} open={true} onClose={vi.fn()} />)
    expect(screen.getByText('High')).toBeInTheDocument()
  })
})
