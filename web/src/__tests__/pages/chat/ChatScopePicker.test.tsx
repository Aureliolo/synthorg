import { render, screen } from '@testing-library/react'
import type { AlertSummary, ProposalSummary } from '@/api/endpoints/meta'
import { ChatScopePicker, type ChatScopeValue } from '@/pages/chat/ChatScopePicker'

const proposal: ProposalSummary = {
  id: 'proposal-1',
  title: 'Tune retry backoff',
  action_type: 'signals.proposal',
  status: 'pending',
  risk_level: 'medium',
  requested_by: 'meta_improvement_service',
  created_at: '2026-06-20T12:00:00Z',
}

const alert: AlertSummary = {
  id: 'alert-1',
  severity: 'warning',
  alert_type: 'inflection',
  description: 'Quality dropped sharply',
  affected_domains: ['performance'],
  signal_context: {},
  recommended_action: null,
  emitted_at: '2026-06-20T12:00:00Z',
}

describe('ChatScopePicker', () => {
  it('renders the chip when a value is set even if proposals/alerts are both empty', () => {
    const value: ChatScopeValue = { kind: 'proposal', id: 'proposal-1', label: 'Tune retry backoff' }
    render(
      <ChatScopePicker
        proposals={[]}
        alerts={[]}
        value={value}
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByText(/Scoped to:/)).toBeInTheDocument()
    expect(screen.getByText('Tune retry backoff')).toBeInTheDocument()
  })

  it('renders nothing when there is no value and no proposals/alerts', () => {
    const { container } = render(
      <ChatScopePicker proposals={[]} alerts={[]} value={null} onChange={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('moves focus to the clear button when a scope is selected', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <ChatScopePicker proposals={[proposal]} alerts={[alert]} value={null} onChange={onChange} />,
    )
    const value: ChatScopeValue = { kind: 'proposal', id: proposal.id, label: proposal.title }
    rerender(
      <ChatScopePicker proposals={[proposal]} alerts={[alert]} value={value} onChange={onChange} />,
    )
    expect(screen.getByRole('button', { name: 'Clear chat scope' })).toHaveFocus()
  })

  it('restores focus to the picker container after clearing the scope', () => {
    const onChange = vi.fn()
    const value: ChatScopeValue = { kind: 'proposal', id: proposal.id, label: proposal.title }
    const { rerender, container } = render(
      <ChatScopePicker proposals={[proposal]} alerts={[alert]} value={value} onChange={onChange} />,
    )
    rerender(
      <ChatScopePicker proposals={[proposal]} alerts={[alert]} value={null} onChange={onChange} />,
    )
    const picker = container.querySelector('[tabindex="-1"]')
    expect(picker).toHaveFocus()
  })
})
