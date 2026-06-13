/**
 * Workflow mock-data builders.
 */

/**
 * Workflow definition, mirroring ``WorkflowDefinition`` from
 * ``@/api/types`` (the wire shape ``listWorkflows`` paginates over
 * ``/workflows``). The earlier minimal shape (``active`` / numeric
 * ``version``) did not match, so the list never rendered.
 */
export interface MockWorkflow {
  id: string
  name: string
  description: string
  workflow_type: string
  version: string
  inputs: unknown[]
  outputs: unknown[]
  is_subworkflow: boolean
  nodes: unknown[]
  edges: unknown[]
  created_by: string
  created_at: string
  updated_at: string
  revision: number
}

export function makeWorkflow(overrides: Partial<MockWorkflow> = {}): MockWorkflow {
  return {
    id: 'workflow-001',
    name: 'Daily standup',
    description: 'Morning sync',
    workflow_type: 'sequential_pipeline',
    version: '1',
    inputs: [],
    outputs: [],
    is_subworkflow: false,
    nodes: [],
    edges: [],
    created_by: 'user-1',
    created_at: '2026-04-01T12:00:00Z',
    updated_at: '2026-04-01T12:00:00Z',
    revision: 1,
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
