/**
 * Client-side YAML preview generation.
 *
 * Mirrors the backend export logic for live preview in the editor. Uses
 * js-yaml (already a project dependency).
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

const SKIP_TYPES = new Set(['start', 'end'])

/** Kahn's algorithm for topological ordering. */
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
  return drainTopoQueue(queue, adj, inDegree)
}

function drainTopoQueue(
  queue: string[],
  adj: Map<string, string[]>,
  inDegree: Map<string, number>,
): string[] {
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

/** Generate a YAML string from the editor's nodes and edges. */
export function generateYamlPreview(
  nodes: Node[],
  edges: Edge[],
  workflowName: string,
  workflowType: string,
): string {
  const ctx = buildNodeContext(nodes, edges)
  const steps: StepData[] = []
  for (const nodeId of ctx.sorted) {
    const node = ctx.nodeMap.get(nodeId)
    if (!node || SKIP_TYPES.has(node.type ?? '')) continue
    steps.push(buildStepData(node, ctx))
  }
  const document = {
    workflow_definition: {
      name: workflowName,
      workflow_type: workflowType,
      steps,
    },
  }
  let output = yaml.dump(document, { sortKeys: false, noRefs: true })
  if (ctx.hasCycle) {
    output = '# WARNING: Cycle detected. Some nodes omitted from preview.\n' + output
  }
  return output
}

interface NodeContext {
  nodeMap: Map<string, Node>
  edges: Edge[]
  incoming: Map<string, string[]>
  outgoing: Map<string, Edge[]>
  sorted: string[]
  hasCycle: boolean
}

function buildNodeContext(nodes: Node[], edges: Edge[]): NodeContext {
  const nodeMap = new Map(nodes.map((n) => [n.id, n]))
  const allIds = nodes.map((n) => n.id)
  const sorted = topologicalSort(allIds, edges)
  const incoming = new Map<string, string[]>()
  for (const edge of edges) {
    const list = incoming.get(edge.target) ?? []
    list.push(edge.source)
    incoming.set(edge.target, list)
  }
  const outgoing = new Map<string, Edge[]>()
  for (const edge of edges) {
    const list = outgoing.get(edge.source) ?? []
    list.push(edge)
    outgoing.set(edge.source, list)
  }
  return {
    nodeMap,
    edges,
    incoming,
    outgoing,
    sorted,
    hasCycle: sorted.length < allIds.length,
  }
}

function buildStepData(node: Node, ctx: NodeContext): StepData {
  const config = (node.data as Record<string, unknown>)?.config as
    | Record<string, unknown>
    | undefined
  const step: StepData = { id: node.id, type: node.type ?? 'task' }
  STEP_FIELD_FILLERS[node.type ?? '']?.({ step, config, node, ctx })
  fillDependsOn(step, node.id, ctx)
  return step
}

interface StepFillerArgs {
  step: StepData
  config: Record<string, unknown> | undefined
  node: Node
  ctx: NodeContext
}

const STEP_FIELD_FILLERS: Readonly<Record<string, (args: StepFillerArgs) => void>> = {
  task: ({ step, config }) => {
    if (!config) return
    copyIfPresent(step, config, ['title', 'task_type', 'priority', 'complexity', 'coordination_topology'])
  },
  conditional: ({ step, config }) => {
    if (config?.condition_expression) step.condition = config.condition_expression
  },
  parallel_split: ({ step, config, node, ctx }) => {
    const branches = (ctx.outgoing.get(node.id) ?? [])
      .filter((e) => (e.data as Record<string, unknown> | undefined)?.edgeType === 'parallel_branch')
      .map((e) => e.target)
    if (branches.length > 0) step.branches = branches
    if (config?.max_concurrency) step.max_concurrency = config.max_concurrency
  },
  parallel_join: ({ step, config }) => {
    step.join_strategy = (config?.join_strategy as string) || 'all'
  },
  agent_assignment: ({ step, config }) => {
    if (!config) return
    if (config.routing_strategy) step.strategy = config.routing_strategy
    if (config.role_filter) step.role = config.role_filter
    if (config.agent_name) step.agent_name = config.agent_name
  },
  subworkflow: ({ step, config }) => {
    if (!config) return
    copyIfPresent(step, config, [
      'subworkflow_id',
      'version',
      'input_bindings',
      'output_bindings',
    ])
  },
}

function copyIfPresent(
  step: StepData,
  config: Record<string, unknown>,
  keys: readonly string[],
): void {
  for (const key of keys) {
    if (config[key]) step[key] = config[key]
  }
}

function fillDependsOn(step: StepData, nodeId: string, ctx: NodeContext): void {
  const depEntries: DependsOnEntry[] = []
  for (const srcId of ctx.incoming.get(nodeId) ?? []) {
    const srcNode = ctx.nodeMap.get(srcId)
    if (!srcNode || SKIP_TYPES.has(srcNode.type ?? '')) continue
    depEntries.push(buildDependsOnEntry(srcId, nodeId, ctx.edges))
  }
  if (depEntries.length > 0) step.depends_on = depEntries
}

function buildDependsOnEntry(
  srcId: string,
  nodeId: string,
  edges: readonly Edge[],
): DependsOnEntry {
  const edge = edges.find((e) => e.source === srcId && e.target === nodeId)
  const edgeType = (edge?.data as Record<string, unknown> | undefined)?.edgeType as
    | string
    | undefined
  if (edgeType === 'conditional_true') return { id: srcId, branch: 'true' }
  if (edgeType === 'conditional_false') return { id: srcId, branch: 'false' }
  return srcId
}
