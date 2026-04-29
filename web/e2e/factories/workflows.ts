/**
 * Workflow mock-data builders.
 */

export interface MockWorkflow {
  id: string
  name: string
  description: string
  active: boolean
  version: number
}

export function makeWorkflow(overrides: Partial<MockWorkflow> = {}): MockWorkflow {
  return {
    id: 'workflow-001',
    name: 'Daily standup',
    description: 'Morning sync',
    active: true,
    version: 1,
    ...overrides,
  }
}

export interface MockWorkflowExecution {
  id: string
  workflow_id: string
  status: 'pending' | 'running' | 'success' | 'failed' | 'cancelled'
  started_at: string
  finished_at: string | null
}

export function makeWorkflowExecution(
  overrides: Partial<MockWorkflowExecution> = {},
): MockWorkflowExecution {
  return {
    id: 'exec-001',
    workflow_id: 'workflow-001',
    status: 'pending',
    started_at: '2026-04-01T12:00:00Z',
    finished_at: null,
    ...overrides,
  }
}

export interface MockWorkflowNode {
  id: string
  type: 'task' | 'condition' | 'subworkflow'
  label: string
}

export function makeWorkflowNode(
  overrides: Partial<MockWorkflowNode> = {},
): MockWorkflowNode {
  return {
    id: 'node-001',
    type: 'task',
    label: 'Run task',
    ...overrides,
  }
}
