/**
 * Tests for YAML-to-nodes parser (depends_on branch metadata support).
 */
import { describe, it, expect } from 'vitest'
import { parseYamlToNodesEdges } from '@/pages/workflow-editor/yaml-to-nodes'

function findEdge(
  edges: { source: string; target: string; data?: Record<string, unknown> }[],
  source: string,
  target: string,
) {
  return edges.find((e) => e.source === source && e.target === target)
}

describe('parseYamlToNodesEdges depends_on', () => {
  it('parses plain string depends_on (backward compat)', () => {
    const yaml = `
workflow_definition:
  name: test
  workflow_type: agile
  steps:
    - id: step_a
      type: task
    - id: step_b
      type: task
      depends_on:
        - step_a
`
    const result = parseYamlToNodesEdges(yaml)
    expect(result.errors).toHaveLength(0)
    const edge = findEdge(result.edges, 'step_a', 'step_b')
    expect(edge).toBeDefined()
    expect(edge!.data?.['edgeType']).toBe('sequential')
  })

  it('parses object depends_on with branch true', () => {
    const yaml = `
workflow_definition:
  name: test
  workflow_type: agile
  steps:
    - id: check
      type: conditional
      condition: env == prod
    - id: deploy
      type: task
      depends_on:
        - id: check
          branch: "true"
`
    const result = parseYamlToNodesEdges(yaml)
    expect(result.errors).toHaveLength(0)
    const edge = findEdge(result.edges, 'check', 'deploy')
    expect(edge).toBeDefined()
    expect(edge!.data?.['edgeType']).toBe('conditional_true')
    expect(edge!.data?.['branch']).toBe('true')
  })

  it('parses object depends_on with branch false', () => {
    const yaml = `
workflow_definition:
  name: test
  workflow_type: agile
  steps:
    - id: check
      type: conditional
      condition: env == prod
    - id: rollback
      type: task
      depends_on:
        - id: check
          branch: "false"
`
    const result = parseYamlToNodesEdges(yaml)
    expect(result.errors).toHaveLength(0)
    const edge = findEdge(result.edges, 'check', 'rollback')
    expect(edge).toBeDefined()
    expect(edge!.data?.['edgeType']).toBe('conditional_false')
    expect(edge!.data?.['branch']).toBe('false')
  })

  it('supports mixed string and object depends_on entries', () => {
    const yaml = `
workflow_definition:
  name: test
  workflow_type: agile
  steps:
    - id: setup
      type: task
    - id: check
      type: conditional
      condition: ready == yes
    - id: run
      type: task
      depends_on:
        - setup
        - id: check
          branch: "true"
`
    const result = parseYamlToNodesEdges(yaml)
    expect(result.errors).toHaveLength(0)
    const seqEdge = findEdge(result.edges, 'setup', 'run')
    expect(seqEdge).toBeDefined()
    expect(seqEdge!.data?.['edgeType']).toBe('sequential')
    const condEdge = findEdge(result.edges, 'check', 'run')
    expect(condEdge).toBeDefined()
    expect(condEdge!.data?.['edgeType']).toBe('conditional_true')
  })

  it('falls back to counter inference for object without branch', () => {
    const yaml = `
workflow_definition:
  name: test
  workflow_type: agile
  steps:
    - id: check
      type: conditional
      condition: ready
    - id: yes_step
      type: task
      depends_on:
        - id: check
    - id: no_step
      type: task
      depends_on:
        - id: check
`
    const result = parseYamlToNodesEdges(yaml)
    expect(result.errors).toHaveLength(0)
    const trueEdge = findEdge(result.edges, 'check', 'yes_step')
    const falseEdge = findEdge(result.edges, 'check', 'no_step')
    expect(trueEdge!.data?.['edgeType']).toBe('conditional_true')
    expect(falseEdge!.data?.['edgeType']).toBe('conditional_false')
  })

  it('reports error for invalid depends_on entry type', () => {
    const yaml = `
workflow_definition:
  name: test
  workflow_type: agile
  steps:
    - id: step_a
      type: task
    - id: step_b
      type: task
      depends_on:
        - [nested, array]
`
    const result = parseYamlToNodesEdges(yaml)
    expect(result.errors.length).toBeGreaterThan(0)
    expect(result.errors[0]).toContain('invalid dependency')
  })

  it('warns for unrecognized branch value and falls back to inference', () => {
    const yaml = `
workflow_definition:
  name: test
  workflow_type: agile
  steps:
    - id: check
      type: conditional
      condition: ready
    - id: step_b
      type: task
      depends_on:
        - id: check
          branch: "maybe"
`
    const result = parseYamlToNodesEdges(yaml)
    expect(result.errors).toHaveLength(0)
    expect(result.warnings.length).toBeGreaterThan(0)
    expect(result.warnings[0]).toContain('unrecognized branch')
    // Falls back to inference: first conditional edge is conditional_true
    const edge = findEdge(result.edges, 'check', 'step_b')
    expect(edge!.data?.['edgeType']).toBe('conditional_true')
  })

  it('reports error for object with empty id', () => {
    const yaml = `
workflow_definition:
  name: test
  workflow_type: agile
  steps:
    - id: step_a
      type: task
    - id: step_b
      type: task
      depends_on:
        - id: ""
          branch: "true"
`
    const result = parseYamlToNodesEdges(yaml)
    expect(result.errors.length).toBeGreaterThan(0)
    expect(result.errors[0]).toContain('empty dependency')
  })
})
