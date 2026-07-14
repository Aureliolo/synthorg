/**
 * Agent mock-data builders.
 *
 * Mirrors the wire shape returned by ``/api/v1/agents`` so route
 * mocks can use these directly without manual ApiResponse wrapping.
 */

export interface MockAgent {
  id: string
  name: string
  role: string
  department: string
  status: 'active' | 'idle' | 'paused' | 'terminated'
  current_task_id: string | null
  /** Raw model config dict, mirroring the wire ``AgentConfig.model``. */
  model: Record<string, unknown>
  /** Raw personality config dict, mirroring ``AgentConfig.personality``. */
  personality: Record<string, unknown>
  /** Raw model requirement dict, mirroring ``AgentConfig.model_requirement``. */
  model_requirement: Record<string, unknown>
  personality_preset: string | null
  tier: 'large' | 'medium' | 'small' | null
}

export function makeAgent(overrides: Partial<MockAgent> = {}): MockAgent {
  return {
    id: 'agent-001',
    name: 'Alice',
    role: 'engineer',
    department: 'engineering',
    status: 'active',
    current_task_id: null,
    model: { model_id: 'example-medium-001' },
    personality: { traits: ['pragmatic', 'thorough'] },
    model_requirement: { requires_tools: true },
    personality_preset: 'balanced',
    tier: 'medium',
    ...overrides,
  }
}

export function makeAgentList(count: number = 3): MockAgent[] {
  if (!Number.isInteger(count) || count < 0) {
    throw new RangeError(
      `makeAgentList: count must be a non-negative integer; got ${count}`,
    )
  }
  return Array.from({ length: count }, (_, idx) =>
    makeAgent({
      id: `agent-${String(idx + 1).padStart(3, '0')}`,
      name: ['Alice', 'Bob', 'Carol', 'Dan', 'Eve'][idx % 5] ?? `Agent ${idx + 1}`,
    }),
  )
}
