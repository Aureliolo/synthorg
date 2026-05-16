import { http, HttpResponse } from 'msw'
import type { AuditEntry } from '@/api/types/dtos.gen'

function entry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    action_type: 'invoke_tool',
    agent_id: 'agent_001',
    approval_id: null,
    arguments_hash: 'sha256:abc',
    confidence: 'high',
    evaluation_duration_ms: 12,
    id: 'audit_001',
    matched_rules: [],
    reason: 'No rule matched; default allow.',
    risk_level: 'low',
    task_id: 'task_001',
    timestamp: '2026-05-16T12:00:00Z',
    tool_category: 'safe',
    tool_name: 'file_system.read',
    verdict: 'allow',
    ...overrides,
  } as AuditEntry
}

export const auditHandlers = [
  http.get('/api/v1/security/audit', () =>
    HttpResponse.json({
      data: [
        entry({ id: 'audit_001' }),
        entry({ id: 'audit_002', verdict: 'deny', reason: 'Risk too high.' }),
      ],
      error: null,
      error_detail: null,
      pagination: { limit: 50, next_cursor: null, has_more: false },
      success: true,
    }),
  ),
]
