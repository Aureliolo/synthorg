/**
 * Tests for workflow-to-YAML exporter (depends_on branch metadata).
 */
import { describe, it, expect } from 'vitest'
import fc from 'fast-check'
import yaml from 'js-yaml'
import type { Node, Edge } from '@xyflow/react'
import { generateYamlPreview } from '@/pages/workflow-editor/workflow-to-yaml'

function makeNode(id: string, type: string, config?: Record<string, unknown>): Node {
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data: { label: id, config: config ?? {} },
  }
}

function makeEdge(
  source: string,
  target: string,
  edgeType: string = 'sequential',
  branch?: 'true' | 'false',
): Edge {
  return {
    id: `edge-${source}-${target}`,
    source,
    target,
    type: edgeType === 'sequential' ? 'sequential' : 'conditional',
    data: { edgeType, branch },
  }
}

/** Parse YAML output and return the workflow steps array. */
function parseSteps(output: string): Array<Record<string, unknown>> {
  const parsed = yaml.load(output, { schema: yaml.CORE_SCHEMA }) as {
    workflow_definition: { steps: Array<Record<string, unknown>> }
  }
  return parsed.workflow_definition.steps
}

describe('generateYamlPreview depends_on', () => {
  it('emits plain string for sequential edges', () => {
    const nodes = [
      makeNode('start', 'start'),
      makeNode('step_a', 'task', { title: 'A' }),
      makeNode('step_b', 'task', { title: 'B' }),
      makeNode('end', 'end'),
    ]
    const edges = [
      makeEdge('start', 'step_a'),
      makeEdge('step_a', 'step_b'),
      makeEdge('step_b', 'end'),
    ]
    const output = generateYamlPreview(nodes, edges, 'test', 'agile')
    // Parse back to verify depends_on is a plain string
    expect(output).toContain('depends_on')
    expect(output).toContain('- step_a')
    // Should NOT contain { id: ... } format
    expect(output).not.toContain('branch:')
  })

  it('emits object with branch for conditional edges', () => {
    const nodes = [
      makeNode('start', 'start'),
      makeNode('check', 'conditional', { condition_expression: 'env == prod' }),
      makeNode('yes_step', 'task', { title: 'Yes' }),
      makeNode('no_step', 'task', { title: 'No' }),
      makeNode('end', 'end'),
    ]
    const edges = [
      makeEdge('start', 'check'),
      makeEdge('check', 'yes_step', 'conditional_true', 'true'),
      makeEdge('check', 'no_step', 'conditional_false', 'false'),
      makeEdge('yes_step', 'end'),
      makeEdge('no_step', 'end'),
    ]
    const output = generateYamlPreview(nodes, edges, 'test', 'agile')
    const steps = parseSteps(output)
    const yesStep = steps.find((s) => s['id'] === 'yes_step')
    const noStep = steps.find((s) => s['id'] === 'no_step')
    expect(yesStep).toBeDefined()
    expect(noStep).toBeDefined()
    expect(yesStep!['depends_on']).toEqual([{ id: 'check', branch: 'true' }])
    expect(noStep!['depends_on']).toEqual([{ id: 'check', branch: 'false' }])
  })

  it('emits branches field for parallel_split node (no branch metadata in depends_on)', () => {
    const nodes = [
      makeNode('start', 'start'),
      makeNode('fork', 'parallel_split'),
      makeNode('a', 'task', { title: 'A' }),
      makeNode('b', 'task', { title: 'B' }),
      makeNode('end', 'end'),
    ]
    const edges = [
      makeEdge('start', 'fork'),
      makeEdge('fork', 'a', 'parallel_branch'),
      makeEdge('fork', 'b', 'parallel_branch'),
      makeEdge('a', 'end'),
      makeEdge('b', 'end'),
    ]
    const output = generateYamlPreview(nodes, edges, 'test', 'agile')
    const steps = parseSteps(output)
    // The split node should have a branches field listing targets
    const forkStep = steps.find((s) => s['id'] === 'fork')
    expect(forkStep).toBeDefined()
    expect(forkStep!['branches']).toEqual(expect.arrayContaining(['a', 'b']))
    // Child tasks should have plain string depends_on (no branch metadata)
    const aStep = steps.find((s) => s['id'] === 'a')
    expect(aStep).toBeDefined()
    expect(aStep!['depends_on']).toEqual(['fork'])
    expect(output).not.toContain('branch:')
  })

  it('emits mixed plain strings and objects when both edge types exist', () => {
    const nodes = [
      makeNode('start', 'start'),
      makeNode('setup', 'task', { title: 'Setup' }),
      makeNode('check', 'conditional', { condition_expression: 'ready' }),
      makeNode('run', 'task', { title: 'Run' }),
      makeNode('end', 'end'),
    ]
    const edges = [
      makeEdge('start', 'setup'),
      makeEdge('setup', 'check'),
      makeEdge('check', 'run', 'conditional_true', 'true'),
      makeEdge('run', 'end'),
    ]
    const output = generateYamlPreview(nodes, edges, 'test', 'agile')
    const steps = parseSteps(output)
    // 'run' should have depends_on with { id: check, branch: 'true' }
    const runStep = steps.find((s) => s['id'] === 'run')
    expect(runStep).toBeDefined()
    expect(runStep!['depends_on']).toEqual([{ id: 'check', branch: 'true' }])
    // 'check' should have plain string depends_on from setup
    const checkStep = steps.find((s) => s['id'] === 'check')
    expect(checkStep).toBeDefined()
    expect(checkStep!['depends_on']).toEqual(['setup'])
  })

  it('conditional edges produce {id, branch} and sequential edges produce plain strings (property)', () => {
    // Generate a random chain: start -> N task nodes with sequential
    // edges, one conditional node branching to two tasks, then end.
    const taskCountArb = fc.integer({ min: 1, max: 5 })

    fc.assert(
      fc.property(taskCountArb, (taskCount) => {
        // Build a linear chain of task nodes
        const nodes: Node[] = [makeNode('start', 'start')]
        const edges: Edge[] = []
        let prev = 'start'
        for (let i = 0; i < taskCount; i++) {
          const id = `task_${i}`
          nodes.push(makeNode(id, 'task', { title: id }))
          edges.push(makeEdge(prev, id))
          prev = id
        }
        // Add a conditional branch at the end
        const condId = 'cond'
        nodes.push(makeNode(condId, 'conditional', { condition_expression: 'x' }))
        edges.push(makeEdge(prev, condId))
        nodes.push(makeNode('yes', 'task', { title: 'Yes' }))
        nodes.push(makeNode('no', 'task', { title: 'No' }))
        edges.push(makeEdge(condId, 'yes', 'conditional_true', 'true'))
        edges.push(makeEdge(condId, 'no', 'conditional_false', 'false'))
        nodes.push(makeNode('end', 'end'))
        edges.push(makeEdge('yes', 'end'))
        edges.push(makeEdge('no', 'end'))

        const output = generateYamlPreview(nodes, edges, 'test', 'agile')
        const steps = parseSteps(output)

        for (const step of steps) {
          if (!step['depends_on']) continue
          expect(Array.isArray(step['depends_on'])).toBe(true)
          for (const dep of step['depends_on'] as Array<unknown>) {
            if (typeof dep === 'object' && dep !== null) {
              const obj = dep as Record<string, unknown>
              expect(obj['id']).toBeDefined()
              expect(obj['branch']).toBeDefined()
              expect(obj['branch']).toMatch(/^(true|false)$/)
            } else {
              expect(typeof dep).toBe('string')
            }
          }
        }

        // Conditional targets must have object depends_on
        const yesStep = steps.find((s) => s['id'] === 'yes')
        const noStep = steps.find((s) => s['id'] === 'no')
        expect(yesStep).toBeDefined()
        expect(noStep).toBeDefined()
        expect(typeof (yesStep!['depends_on'] as Array<unknown>)[0]).toBe('object')
        expect(typeof (noStep!['depends_on'] as Array<unknown>)[0]).toBe('object')

        // Sequential tasks must have plain string depends_on
        for (let i = 1; i < taskCount; i++) {
          const step = steps.find((s) => s['id'] === `task_${i}`)
          expect(step).toBeDefined()
          expect(step!['depends_on']).toBeDefined()
          expect(typeof (step!['depends_on'] as Array<unknown>)[0]).toBe('string')
        }
      }),
      { numRuns: 20 },
    )
  })
})
