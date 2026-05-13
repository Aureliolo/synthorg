/**
 * Inter-agent message mock-data builders.
 */

export interface MockMessage {
  id: string
  sender: string
  to: string
  type: 'task_update' | 'broadcast' | 'direct' | 'context_injection'
  priority: 'low' | 'normal' | 'high' | 'urgent'
  channel: string
  parts: ReadonlyArray<{ type: 'text'; text: string } | { type: 'data'; data: Record<string, unknown> }>
  timestamp: string
  metadata: Record<string, unknown>
}

export function makeMessage(overrides: Partial<MockMessage> = {}): MockMessage {
  return {
    id: 'msg-001',
    sender: 'agent-001',
    to: 'agent-002',
    type: 'direct',
    priority: 'normal',
    channel: '#engineering',
    parts: [{ type: 'text', text: 'Hello from another company' }],
    timestamp: '2026-05-13T10:00:00Z',
    metadata: {},
    ...overrides,
  }
}
