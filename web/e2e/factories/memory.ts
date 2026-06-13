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

/**
 * Ontology entity, mirroring ``EntityResponse`` from
 * ``@/api/endpoints/ontology`` -- the catalogue shape the ``/ontology``
 * page actually consumes (``listEntities`` -> ``/ontology/entities``).
 */
export interface MockOntologyEntity {
  name: string
  tier: 'core' | 'user'
  source: 'auto' | 'config' | 'api'
  definition: string
  fields: { name: string; type_hint: string; description: string }[]
  constraints: string[]
  disambiguation: string
  relationships: { target: string; relation: string; description: string }[]
  created_by: string
  created_at: string
  updated_at: string
}

export function makeOntologyEntity(
  overrides: Partial<MockOntologyEntity> = {},
): MockOntologyEntity {
  return {
    name: 'TaskAssignment',
    tier: 'user',
    source: 'api',
    definition: 'A task assigned to an agent.',
    fields: [
      { name: 'priority', type_hint: 'str', description: 'Task priority.' },
    ],
    constraints: [],
    disambiguation: '',
    relationships: [
      { target: 'Agent', relation: 'reports_to', description: 'Owning agent.' },
    ],
    created_by: 'operator',
    created_at: '2026-04-01T12:00:00Z',
    updated_at: '2026-04-01T12:00:00Z',
    ...overrides,
  }
}
