/**
 * Parse YAML workflow definition back into ReactFlow nodes and edges.
 *
 * Reverse of workflow-to-yaml.ts. Reconstructs the visual graph from
 * the flat step list format.
 *
 * Uses a two-pass approach:
 *   Pass 1 -- collect and validate all steps, build seenIds set
 *   Pass 2 -- emit edges only when both source and target exist
 */

import yaml from 'js-yaml'
import type { Node, Edge } from '@xyflow/react'
import type { WorkflowEdgeType } from '@/api/types/workflows'
import { isObject } from '@/utils/type-guards'

export interface ParseResult {
  nodes: Node[]
  edges: Edge[]
  errors: string[]
  warnings: string[]
}

interface YamlStep {
  id?: string
  type?: string
  title?: string
  task_type?: string
  priority?: string
  complexity?: string
  coordination_topology?: string
  condition?: string
  branches?: string[]
  max_concurrency?: number
  join_strategy?: string
  strategy?: string
  role?: string
  agent_name?: string
  subworkflow_id?: string
  version?: string
  input_bindings?: Record<string, unknown>
  output_bindings?: Record<string, unknown>
  depends_on?: (string | number | { id: string; branch?: string })[]
}

/** Validated step with a guaranteed id and type. */
interface ValidatedStep {
  id: string
  type: string
  step: YamlStep
  index: number
}

const VALID_TYPES = new Set([
  'task',
  'agent_assignment',
  'conditional',
  'parallel_split',
  'parallel_join',
  'subworkflow',
])

const AUTO_LAYOUT_X = 250
const AUTO_LAYOUT_Y_START = 200
const AUTO_LAYOUT_Y_STEP = 120

/**
 * Map a backend edge type to the ReactFlow visual edge type used for
 * custom edge component selection.
 */
function edgeTypeToVisualType(edgeType: WorkflowEdgeType): string {
  if (edgeType === 'conditional_true' || edgeType === 'conditional_false') {
    return 'conditional'
  }
  return edgeType
}

/**
 * Infer the edge type from the source step's type and branch index.
 *
 * Used as fallback when depends_on entries lack explicit branch
 * metadata.  When an entry is `{ id, branch }`, the branch field
 * takes precedence over this counter-based inference.
 */
function inferDependsOnEdgeType(
  sourceStep: ValidatedStep,
  branchIndex: number,
): WorkflowEdgeType {
  if (sourceStep.type === 'conditional' && sourceStep.step.condition) {
    return branchIndex === 0 ? 'conditional_true' : 'conditional_false'
  }
  return 'sequential'
}

/**
 * Parse a YAML string into ReactFlow nodes and edges.
 *
 * @param yamlStr - YAML content to parse
 * @param existingPositions - Optional map of nodeId -> position for
 *   preserving layout when round-tripping
 */
export function parseYamlToNodesEdges(
  yamlStr: string,
  existingPositions?: Map<string, { x: number; y: number }>,
): ParseResult {
  const errors: string[] = []
  const warnings: string[] = []
  const nodes: Node[] = []
  const edges: Edge[] = []

  let parsed: unknown
  try {
    parsed = yaml.load(yamlStr, { schema: yaml.CORE_SCHEMA })
  } catch (err) {
    errors.push(`YAML parse error: ${err instanceof Error ? err.message : String(err)}`)
    return { nodes, edges, errors, warnings }
  }

  if (!isObject(parsed)) {
    errors.push('YAML must contain an object')
    return { nodes, edges, errors, warnings }
  }

  const wfDefRaw = parsed.workflow_definition
  if (!isObject(wfDefRaw)) {
    errors.push('Missing "workflow_definition" key')
    return { nodes, edges, errors, warnings }
  }

  const stepsRaw = wfDefRaw.steps
  if (!Array.isArray(stepsRaw)) {
    errors.push('Missing or invalid "steps" array')
    return { nodes, edges, errors, warnings }
  }
  // Element shape is validated per-iteration in the loop below; the
  // array-element narrowing happens at use sites where the script
  // checks ``typeof raw !== 'object' || raw === null`` before reading
  // any field.
  const steps: unknown[] = stepsRaw

  // ---------------------------------------------------------------
  // Pass 1: Collect and validate all steps, build seenIds + stepMap
  // ---------------------------------------------------------------
  const seenIds = new Set<string>()
  const stepMap = new Map<string, ValidatedStep>()
  let autoIdCounter = 0

  const RESERVED_IDS = new Set(['start-1', 'end-1'])

  for (let i = 0; i < steps.length; i++) {
    const raw = steps[i]
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
      const actualType = raw === null ? 'null' : Array.isArray(raw) ? 'array' : typeof raw
      errors.push(`Step ${i + 1} is not an object (got ${actualType})`)
      continue
    }
    // Narrow per-element: ``raw`` was checked above (object, non-null,
    // non-array), so the cast to YamlStep is now safe. The YamlStep
    // fields are all optional; the subsequent code validates each
    // field's runtime type before reading.
    const step = raw as YamlStep

    // Validate and normalize ID
    if (step.id !== undefined && typeof step.id !== 'string') {
      errors.push(`Step ${i + 1} has non-string id (got ${typeof step.id})`)
      continue
    }
    const rawId = typeof step.id === 'string' ? step.id.trim() : ''
    const stepId = rawId || `auto-${++autoIdCounter}`

    if (!rawId) {
      warnings.push(`Step ${i + 1} has no id, auto-generated: ${stepId}`)
    }

    if (RESERVED_IDS.has(stepId)) {
      errors.push(`Step ${i + 1} uses reserved id "${stepId}"`)
      continue
    }

    if (seenIds.has(stepId)) {
      errors.push(`Duplicate step id: ${stepId}`)
      continue
    }

    const stepType = step.type ?? 'task'
    if (!VALID_TYPES.has(stepType)) {
      errors.push(`Unknown step type "${stepType}" for step "${stepId}"`)
      continue
    }

    // Only add to seenIds after type validation passes
    seenIds.add(stepId)
    stepMap.set(stepId, { id: stepId, type: stepType, step, index: i })
  }

  // ---------------------------------------------------------------
  // Build nodes from validated steps
  // ---------------------------------------------------------------
  const startId = 'start-1'
  nodes.push({
    id: startId,
    type: 'start',
    position: existingPositions?.get(startId) ?? { x: AUTO_LAYOUT_X, y: 50 },
    data: { label: 'Start', config: {} },
  })

  const stepIds: string[] = []

  for (const [stepId, validated] of stepMap) {
    stepIds.push(stepId)
    const config = buildConfig(validated.step, validated.type)
    const position = existingPositions?.get(stepId) ?? {
      x: AUTO_LAYOUT_X,
      y: AUTO_LAYOUT_Y_START + validated.index * AUTO_LAYOUT_Y_STEP,
    }

    nodes.push({
      id: stepId,
      type: validated.type,
      position,
      data: { label: stringOrUndef(validated.step.title) ?? stepId, config },
    })
  }

  // ---------------------------------------------------------------
  // Pass 2: Emit edges -- only when both source and target are valid
  // ---------------------------------------------------------------

  // Track how many depends_on edges each conditional source has
  // emitted so we can alternate true/false branches.
  const conditionalBranchCounters = new Map<string, number>()
  const emittedEdges = new Set<string>()

  for (const [stepId, validated] of stepMap) {
    const { step } = validated

    // Edges from depends_on (supports plain strings and { id, branch } objects)
    if (step.depends_on && !Array.isArray(step.depends_on)) {
      errors.push(`Step '${stepId}' has non-array depends_on (got ${typeof step.depends_on})`)
    }
    if (step.depends_on && Array.isArray(step.depends_on)) {
      for (const rawDep of step.depends_on) {
        // Parse entry: string, number, or { id, branch } object
        let depId: string
        let explicitBranch: 'true' | 'false' | undefined

        if (isObject(rawDep) && 'id' in rawDep) {
          const obj: Record<string, unknown> = rawDep
          depId = String(obj.id ?? '').trim()
          const branch = obj.branch !== undefined ? String(obj.branch) : undefined
          if (branch === 'true' || branch === 'false') {
            explicitBranch = branch
          } else if (branch !== undefined) {
            warnings.push(`Step '${stepId}' dependency '${depId}' has unrecognized branch value '${branch}' -- falling back to inference`)
          }
        } else if (typeof rawDep === 'string' || typeof rawDep === 'number') {
          depId = String(rawDep).trim()
        } else {
          errors.push(`Step '${stepId}' has invalid dependency: ${JSON.stringify(rawDep)}`)
          continue
        }

        if (!depId) {
          errors.push(`Step '${stepId}' has empty dependency`)
          continue
        }
        if (!seenIds.has(depId)) {
          errors.push(`Step '${stepId}' references unknown dependency '${depId}'`)
          continue
        }

        const sourceStep = stepMap.get(depId)!
        let edgeType: WorkflowEdgeType

        if (explicitBranch !== undefined) {
          // Explicit branch metadata takes precedence
          edgeType = explicitBranch === 'true' ? 'conditional_true' : 'conditional_false'
          if (sourceStep.type !== 'conditional') {
            warnings.push(`Step '${stepId}': explicit branch '${explicitBranch}' on non-conditional dependency '${depId}'`)
          }
          // Only advance the counter when the true slot is consumed so
          // a subsequent implicit entry correctly gets the false slot.
          // Explicit false does NOT advance -- the true slot is still open.
          if (sourceStep.type === 'conditional' && sourceStep.step.condition && explicitBranch === 'true') {
            const branchIdx = conditionalBranchCounters.get(depId) ?? 0
            conditionalBranchCounters.set(depId, branchIdx + 1)
          }
        } else {
          // Fall back to counter-based inference (backward compat)
          const branchIdx = conditionalBranchCounters.get(depId) ?? 0
          edgeType = inferDependsOnEdgeType(sourceStep, branchIdx)
          if (sourceStep.type === 'conditional' && sourceStep.step.condition) {
            conditionalBranchCounters.set(depId, branchIdx + 1)
          }
        }

        const edgeKey = `${depId}->${stepId}:${edgeType}`
        if (emittedEdges.has(edgeKey)) continue
        emittedEdges.add(edgeKey)

        const visualType = edgeTypeToVisualType(edgeType)
        const isTrue = edgeType === 'conditional_true'
        const isFalse = edgeType === 'conditional_false'

        edges.push({
          id: `edge-${depId}-${stepId}-${edgeType}`,
          source: depId,
          target: stepId,
          type: visualType,
          sourceHandle: isTrue ? 'true' : isFalse ? 'false' : undefined,
          data: {
            edgeType,
            branch: isTrue ? 'true' : isFalse ? 'false' : undefined,
          },
        })
      }
    }

    // Edges from branches (parallel_split)
    if (step.branches && !Array.isArray(step.branches)) {
      errors.push(`Step '${stepId}' has non-array branches (got ${typeof step.branches})`)
    }
    if (step.branches && Array.isArray(step.branches)) {
      for (const rawTarget of step.branches) {
        if (typeof rawTarget !== 'string' && typeof rawTarget !== 'number') {
          errors.push(`Step '${stepId}' has non-string branch target: ${JSON.stringify(rawTarget)}`)
          continue
        }
        const branchTarget = String(rawTarget).trim()
        if (!branchTarget) {
          errors.push(`Step '${stepId}' has empty branch target`)
          continue
        }
        if (!seenIds.has(branchTarget)) {
          errors.push(`Step '${stepId}' references unknown branch target '${branchTarget}'`)
          continue
        }

        const edgeType: WorkflowEdgeType = 'parallel_branch'
        const edgeKey = `${stepId}->${branchTarget}:${edgeType}`
        if (emittedEdges.has(edgeKey)) continue
        emittedEdges.add(edgeKey)

        edges.push({
          id: `edge-${stepId}-${branchTarget}-${edgeType}`,
          source: stepId,
          target: branchTarget,
          type: edgeTypeToVisualType(edgeType),
          data: { edgeType },
        })
      }
    }
  }

  // ---------------------------------------------------------------
  // Synthetic end node
  // ---------------------------------------------------------------
  const endId = 'end-1'
  nodes.push({
    id: endId,
    type: 'end',
    position: existingPositions?.get(endId) ?? {
      x: AUTO_LAYOUT_X,
      y: AUTO_LAYOUT_Y_START + steps.length * AUTO_LAYOUT_Y_STEP,
    },
    data: { label: 'End', config: {} },
  })

  // ---------------------------------------------------------------
  // Connect start node to root steps (those with no incoming edges)
  // ---------------------------------------------------------------
  const hasIncoming = new Set(edges.map((e) => e.target))
  const rootStepIds = stepIds.filter((id) => !hasIncoming.has(id))

  for (const rootId of rootStepIds) {
    edges.push({
      id: `edge-${startId}-${rootId}`,
      source: startId,
      target: rootId,
      type: 'sequential',
      data: { edgeType: 'sequential' as WorkflowEdgeType },
    })
  }

  // Connect leaf steps (those with no outgoing edges) to end
  const hasOutgoing = new Set(edges.map((e) => e.source))
  for (const stepId of stepIds) {
    if (!hasOutgoing.has(stepId)) {
      edges.push({
        id: `edge-${stepId}-${endId}`,
        source: stepId,
        target: endId,
        type: 'sequential',
        data: { edgeType: 'sequential' as WorkflowEdgeType },
      })
    }
  }

  return { nodes, edges, errors, warnings }
}

/** Accept only string values from YAML-parsed data. */
function stringOrUndef(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}

function buildConfig(step: YamlStep, stepType: string): Record<string, unknown> {
  const config: Record<string, unknown> = {}

  if (stepType === 'task') {
    const title = stringOrUndef(step.title)
    if (title) config.title = title
    const taskType = stringOrUndef(step.task_type)
    if (taskType) config.task_type = taskType
    const priority = stringOrUndef(step.priority)
    if (priority) config.priority = priority
    const complexity = stringOrUndef(step.complexity)
    if (complexity) config.complexity = complexity
    const topology = stringOrUndef(step.coordination_topology)
    if (topology) config.coordination_topology = topology
  } else if (stepType === 'conditional') {
    const expr = stringOrUndef(step.condition)
    if (expr) config.condition_expression = expr
  } else if (stepType === 'parallel_split') {
    if (typeof step.max_concurrency === 'number') {
      config.max_concurrency = step.max_concurrency
    }
  } else if (stepType === 'parallel_join') {
    config.join_strategy = stringOrUndef(step.join_strategy) ?? 'all'
  } else if (stepType === 'agent_assignment') {
    const strategy = stringOrUndef(step.strategy)
    if (strategy) config.routing_strategy = strategy
    const role = stringOrUndef(step.role)
    if (role) config.role_filter = role
    const agentName = stringOrUndef(step.agent_name)
    if (agentName) config.agent_name = agentName
  } else if (stepType === 'subworkflow') {
    const subworkflowId = stringOrUndef(step.subworkflow_id)
    if (subworkflowId) config.subworkflow_id = subworkflowId
    const version = stringOrUndef(step.version)
    if (version) config.version = version
    if (
      typeof step.input_bindings === 'object' &&
      step.input_bindings !== null &&
      !Array.isArray(step.input_bindings)
    ) {
      config.input_bindings = step.input_bindings
    }
    if (
      typeof step.output_bindings === 'object' &&
      step.output_bindings !== null &&
      !Array.isArray(step.output_bindings)
    ) {
      config.output_bindings = step.output_bindings
    }
  }

  return config
}
