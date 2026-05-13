/**
 * Client-side YAML preview generation.
 *
 * Mirrors the backend export logic for live preview in the editor.
 * Uses js-yaml (already a project dependency).
 */

import yaml from 'js-yaml'
import type { Node, Edge } from '@xyflow/react'

/** A depends_on entry: plain string or object with explicit branch. */
export type DependsOnEntry = string | { id: string; branch: 'true' | 'false' }

interface StepData {
  id: string
  type: string
  [key: string]: unknown
}

/**
 * Kahn's algorithm for topological ordering.
 */
function topologicalSort(nodeIds: string[], edges: Edge[]): string[] {
  const inDegree = new Map<string, number>()
  const adj = new Map<string, string[]>()

  for (const id of nodeIds) {
    inDegree.set(id, 0)
    adj.set(id, [])
  }

  for (const edge of edges) {
    adj.get(edge.source)?.push(edge.target)
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1)
  }

  const queue = nodeIds.filter((id) => (inDegree.get(id) ?? 0) === 0)
  const result: string[] = []

  while (queue.length > 0) {
    const current = queue.shift()!
    result.push(current)
    for (const neighbor of adj.get(current) ?? []) {
      const deg = (inDegree.get(neighbor) ?? 1) - 1
      inDegree.set(neighbor, deg)
      if (deg === 0) queue.push(neighbor)
    }
  }

  return result
}

/**
 * Generate a YAML string from the editor's nodes and edges.
 */
export function generateYamlPreview(
  nodes: Node[],
  edges: Edge[],
  workflowName: string,
  workflowType: string,
): string {
  const skipTypes = new Set(['start', 'end'])
  const nodeMap = new Map(nodes.map((n) => [n.id, n]))

  const allIds = nodes.map((n) => n.id)
  const sorted = topologicalSort(allIds, edges)
  const hasCycle = sorted.length < allIds.length

  // Build reverse adjacency (incoming edges per node)
  const incoming = new Map<string, string[]>()
  for (const edge of edges) {
    const list = incoming.get(edge.target) ?? []
    list.push(edge.source)
    incoming.set(edge.target, list)
  }

  // Build outgoing edges per node
  const outgoing = new Map<string, Edge[]>()
  for (const edge of edges) {
    const list = outgoing.get(edge.source) ?? []
    list.push(edge)
    outgoing.set(edge.source, list)
  }

  const steps: StepData[] = []

  for (const nodeId of sorted) {
    const node = nodeMap.get(nodeId)
    if (!node || skipTypes.has(node.type ?? '')) continue

    const config = (node.data as Record<string, unknown>)?.config as Record<string, unknown> | undefined
    const step: StepData = { id: nodeId, type: node.type ?? 'task' }

    // Add config fields based on type
    if (node.type === 'task' && config) {
      if (config.title) step.title = config.title
      if (config.task_type) step.task_type = config.task_type
      if (config.priority) step.priority = config.priority
      if (config.complexity) step.complexity = config.complexity
      if (config.coordination_topology) step.coordination_topology = config.coordination_topology
    } else if (node.type === 'conditional' && config?.condition_expression) {
      step.condition = config.condition_expression
    } else if (node.type === 'parallel_split') {
      const branches = (outgoing.get(nodeId) ?? [])
        .filter((e) => e.data?.edgeType === 'parallel_branch')
        .map((e) => e.target)
      if (branches.length > 0) step.branches = branches
      if (config?.max_concurrency) step.max_concurrency = config.max_concurrency
    } else if (node.type === 'parallel_join') {
      step.join_strategy = (config?.join_strategy as string) || 'all'
    } else if (node.type === 'agent_assignment' && config) {
      if (config.routing_strategy) step.strategy = config.routing_strategy
      if (config.role_filter) step.role = config.role_filter
      if (config.agent_name) step.agent_name = config.agent_name
    } else if (node.type === 'subworkflow' && config) {
      if (config.subworkflow_id) step.subworkflow_id = config.subworkflow_id
      if (config.version) step.version = config.version
      if (config.input_bindings) step.input_bindings = config.input_bindings
      if (config.output_bindings) step.output_bindings = config.output_bindings
    }

    // Dependencies (skip START/END), with explicit branch metadata
    // for conditional edges to ensure round-trip fidelity.
    const depEntries: DependsOnEntry[] = []
    for (const srcId of incoming.get(nodeId) ?? []) {
      const srcNode = nodeMap.get(srcId)
      if (!srcNode || skipTypes.has(srcNode.type ?? '')) continue
      // Find the edge from srcId to this node
      const edge = edges.find((e) => e.source === srcId && e.target === nodeId)
      const edgeType = (edge?.data as Record<string, unknown> | undefined)?.edgeType as string | undefined
      if (edgeType === 'conditional_true') {
        depEntries.push({ id: srcId, branch: 'true' })
      } else if (edgeType === 'conditional_false') {
        depEntries.push({ id: srcId, branch: 'false' })
      } else {
        depEntries.push(srcId)
      }
    }
    if (depEntries.length > 0) step.depends_on = depEntries

    steps.push(step)
  }

  const document = {
    workflow_definition: {
      name: workflowName,
      workflow_type: workflowType,
      steps,
    },
  }

  let output = yaml.dump(document, { sortKeys: false, noRefs: true })
  if (hasCycle) {
    output = '# WARNING: Cycle detected. Some nodes omitted from preview.\n' + output
  }
  return output
}
