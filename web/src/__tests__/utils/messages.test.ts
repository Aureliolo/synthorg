import {
  groupMessagesByDate,
  groupMessagesByThread,
  getDateGroupLabel,
  getMessageTypeLabel,
  getMessagePriorityColor,
  getPriorityDotClass,
  getPriorityBadgeClasses,
  getChannelTypeLabel,
  filterMessages,
  messageText,
  partsToAttachments,
} from '@/utils/messages'
import { makeMessage } from '../helpers/factories'

describe('getMessageTypeLabel', () => {
  it('returns human-readable labels for all types', () => {
    expect(getMessageTypeLabel('task_update')).toBe('Task Update')
    expect(getMessageTypeLabel('delegation')).toBe('Delegation')
    expect(getMessageTypeLabel('meeting_contribution')).toBe('Meeting')
    expect(getMessageTypeLabel('hr_notification')).toBe('HR Notice')
  })
})

describe('getMessagePriorityColor', () => {
  it('returns warning for high priority', () => {
    expect(getMessagePriorityColor('high')).toBe('warning')
  })

  it('returns danger for urgent priority', () => {
    expect(getMessagePriorityColor('urgent')).toBe('danger')
  })

  it('returns null for normal and low priorities', () => {
    expect(getMessagePriorityColor('normal')).toBeNull()
    expect(getMessagePriorityColor('low')).toBeNull()
  })
})

describe('getPriorityDotClass', () => {
  it('returns bg class for each color', () => {
    expect(getPriorityDotClass('warning')).toBe('bg-warning')
    expect(getPriorityDotClass('danger')).toBe('bg-danger')
    expect(getPriorityDotClass('success')).toBe('bg-success')
    expect(getPriorityDotClass('accent')).toBe('bg-accent')
  })
})

describe('getPriorityBadgeClasses', () => {
  it('returns border+bg+text classes for each color', () => {
    const warning = getPriorityBadgeClasses('warning')
    expect(warning).toContain('border-warning')
    expect(warning).toContain('bg-warning')
    expect(warning).toContain('text-warning')

    const danger = getPriorityBadgeClasses('danger')
    expect(danger).toContain('border-danger')
    expect(danger).toContain('text-danger')
  })
})

describe('getChannelTypeLabel', () => {
  it('returns correct labels', () => {
    expect(getChannelTypeLabel('topic')).toBe('Topics')
    expect(getChannelTypeLabel('direct')).toBe('Direct')
    expect(getChannelTypeLabel('broadcast')).toBe('Broadcast')
  })
})

describe('getDateGroupLabel', () => {
  it('returns "Today" for today', () => {
    const now = new Date()
    const key = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
    expect(getDateGroupLabel(key)).toBe('Today')
  })

  it('returns "Yesterday" for yesterday', () => {
    const d = new Date()
    d.setDate(d.getDate() - 1)
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    expect(getDateGroupLabel(key)).toBe('Yesterday')
  })

  it('returns formatted date for older dates', () => {
    const label = getDateGroupLabel('2026-01-15')
    expect(label).toContain('Jan')
    expect(label).toContain('15')
    expect(label).toContain('2026')
  })

  it('returns "Unknown" for invalid key', () => {
    expect(getDateGroupLabel('unknown')).toBe('Unknown')
  })
})

describe('groupMessagesByDate', () => {
  it('groups messages by date', () => {
    const msgs = [
      makeMessage('1', { timestamp: '2026-03-28T10:00:00Z' }),
      makeMessage('2', { timestamp: '2026-03-28T14:00:00Z' }),
      makeMessage('3', { timestamp: '2026-03-27T09:00:00Z' }),
    ]
    const groups = groupMessagesByDate(msgs)
    expect(groups.size).toBe(2)
    expect(groups.get('2026-03-28')).toHaveLength(2)
    expect(groups.get('2026-03-27')).toHaveLength(1)
  })

  it('returns empty map for empty array', () => {
    const groups = groupMessagesByDate([])
    expect(groups.size).toBe(0)
  })

  it('groups messages with invalid timestamps under "unknown"', () => {
    const msgs = [
      makeMessage('1', { timestamp: 'not-a-date' }),
      makeMessage('2', { timestamp: '2026-03-28T10:00:00Z' }),
    ]
    const groups = groupMessagesByDate(msgs)
    expect(groups.get('unknown')).toHaveLength(1)
    expect(groups.get('2026-03-28')).toHaveLength(1)
  })
})

describe('groupMessagesByThread', () => {
  it('groups messages by task_id', () => {
    const msgs = [
      makeMessage('1', { metadata: { task_id: 'task-1', project_id: null, tokens_used: null, cost: null, extra: [] } }),
      makeMessage('2', { metadata: { task_id: 'task-1', project_id: null, tokens_used: null, cost: null, extra: [] } }),
      makeMessage('3', { metadata: { task_id: null, project_id: null, tokens_used: null, cost: null, extra: [] } }),
    ]
    const { threads, standalone } = groupMessagesByThread(msgs)
    expect(threads.size).toBe(1)
    expect(threads.get('task-1')).toHaveLength(2)
    expect(standalone).toHaveLength(1)
  })

  it('puts all messages in standalone when no task_ids', () => {
    const msgs = [makeMessage('1'), makeMessage('2')]
    const { threads, standalone } = groupMessagesByThread(msgs)
    expect(threads.size).toBe(0)
    expect(standalone).toHaveLength(2)
  })

  it('handles empty array', () => {
    const { threads, standalone } = groupMessagesByThread([])
    expect(threads.size).toBe(0)
    expect(standalone).toHaveLength(0)
  })
})

describe('filterMessages', () => {
  const msgs = [
    makeMessage('1', { type: 'task_update', priority: 'normal', text: 'API endpoint done', sender: 'alice' }),
    makeMessage('2', { type: 'delegation', priority: 'high', text: 'Please review code', sender: 'bob' }),
    makeMessage('3', { type: 'task_update', priority: 'urgent', text: 'Hotfix deployed', sender: 'alice' }),
  ]

  it('filters by type', () => {
    const result = filterMessages(msgs, { type: 'delegation' })
    expect(result).toHaveLength(1)
    expect(result[0]!.id).toBe('2')
  })

  it('filters by priority', () => {
    const result = filterMessages(msgs, { priority: 'high' })
    expect(result).toHaveLength(1)
    expect(result[0]!.id).toBe('2')
  })

  it('filters by search in content', () => {
    const result = filterMessages(msgs, { search: 'hotfix' })
    expect(result).toHaveLength(1)
    expect(result[0]!.id).toBe('3')
  })

  it('filters by search in sender', () => {
    const result = filterMessages(msgs, { search: 'bob' })
    expect(result).toHaveLength(1)
    expect(result[0]!.id).toBe('2')
  })

  it('combines multiple filters', () => {
    const result = filterMessages(msgs, { type: 'task_update', search: 'api' })
    expect(result).toHaveLength(1)
    expect(result[0]!.id).toBe('1')
  })

  it('returns all messages with no filters', () => {
    const result = filterMessages(msgs, {})
    expect(result).toHaveLength(3)
  })

  it('filters by search in to field', () => {
    const msgsWithTo = [
      makeMessage('1', { to: '#engineering', text: 'hello', sender: 'alice' }),
      makeMessage('2', { to: '#product', text: 'world', sender: 'bob' }),
    ]
    const result = filterMessages(msgsWithTo, { search: 'product' })
    expect(result).toHaveLength(1)
    expect(result[0]!.id).toBe('2')
  })
})

describe('messageText', () => {
  it('returns the canonical text accessor', () => {
    const msg = makeMessage('1', { text: 'body content' })
    expect(messageText(msg)).toBe('body content')
  })
})

describe('partsToAttachments', () => {
  it('drops text parts (body, not attachment)', () => {
    expect(
      partsToAttachments([{ type: 'text', text: 'hi' }]),
    ).toEqual([])
  })

  it('maps data -> artifact with a ref from common keys', () => {
    expect(
      partsToAttachments([{ type: 'data', data: { ref: 'pr-42' } }]),
    ).toEqual([{ type: 'artifact', ref: 'pr-42' }])
    expect(
      partsToAttachments([{ type: 'data', data: { id: 'd-1' } }]),
    ).toEqual([{ type: 'artifact', ref: 'd-1' }])
    expect(
      partsToAttachments([{ type: 'data', data: {} }]),
    ).toEqual([{ type: 'artifact', ref: 'data' }])
  })

  it('maps file -> file and uri -> link by uri', () => {
    expect(
      partsToAttachments([
        { type: 'file', uri: 'report.html', mime_type: 'text/html' },
        { type: 'uri', uri: 'https://example.com' },
      ]),
    ).toEqual([
      { type: 'file', ref: 'report.html' },
      { type: 'link', ref: 'https://example.com' },
    ])
  })

  it('preserves order across a mixed parts list', () => {
    expect(
      partsToAttachments([
        { type: 'text', text: 'body' },
        { type: 'uri', uri: 'https://a' },
        { type: 'data', data: { name: 'art' } },
      ]),
    ).toEqual([
      { type: 'link', ref: 'https://a' },
      { type: 'artifact', ref: 'art' },
    ])
  })

  it('returns empty for empty parts', () => {
    expect(partsToAttachments([])).toEqual([])
  })
})
