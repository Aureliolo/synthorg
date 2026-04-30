/**
 * Memory and ontology mock-data builders.
 */

export interface MockMemoryEntry {
  id: string
  agent_id: string
  category: 'episodic' | 'semantic' | 'procedural'
  content: string
  created_at: string
}

export function makeMemoryEntry(
  overrides: Partial<MockMemoryEntry> = {},
): MockMemoryEntry {
  return {
    id: 'memory-001',
    agent_id: 'agent-001',
    category: 'semantic',
    content: 'Always validate inputs before processing.',
    created_at: '2026-04-01T12:00:00Z',
    ...overrides,
  }
}

export interface MockOntologyFact {
  id: string
  entity: string
  relation: string
  target: string
}

export function makeOntologyFact(
  overrides: Partial<MockOntologyFact> = {},
): MockOntologyFact {
  return {
    id: 'fact-001',
    entity: 'agent-001',
    relation: 'reports_to',
    target: 'agent-002',
    ...overrides,
  }
}
