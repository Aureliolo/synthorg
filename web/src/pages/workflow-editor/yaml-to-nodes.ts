/**
 * Parse YAML workflow definition back into ReactFlow nodes and edges.
 *
 * Reverse of workflow-to-yaml.ts. Reconstructs the visual graph from the
 * flat step list format using a two-pass approach (validate, then emit).
 */
import yaml from 'js-yaml'
import type { Node, Edge } from '@xyflow/react'
import { isObject } from '@/utils/type-guards'
import {
  connectStartAndEnd,
  emitEdgesFromStepMap,
} from './yaml-edge-emit'
import { buildStepConfig, stringOrUndef } from './yaml-step-config'

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
const RESERVED_IDS = new Set(['start-1', 'end-1'])
const AUTO_LAYOUT_X = 250
const AUTO_LAYOUT_Y_START = 200
const AUTO_LAYOUT_Y_STEP = 120

interface ParseAccumulator {
  errors: string[]
  warnings: string[]
}

export function parseYamlToNodesEdges(
  yamlStr: string,
  existingPositions?: Map<string, { x: number; y: number }>,
): ParseResult {
  const acc: ParseAccumulator = { errors: [], warnings: [] }
  const stepsRaw = parseYamlDocument(yamlStr, acc)
  if (!stepsRaw) return { nodes: [], edges: [], ...acc }
  const stepMap = validateStepsToMap(stepsRaw, acc)
  const nodes = buildNodesFromStepMap(stepMap, stepsRaw.length, existingPositions)
  const edges = emitEdgesFromStepMap(stepMap, acc)
  connectStartAndEnd(stepMap, edges)
  return { nodes, edges, errors: acc.errors, warnings: acc.warnings }
}

function parseYamlDocument(
  yamlStr: string,
  acc: ParseAccumulator,
): unknown[] | null {
  let parsed: unknown
  try {
    parsed = yaml.load(yamlStr, { schema: yaml.CORE_SCHEMA })
  } catch (err) {
    acc.errors.push(
      `YAML parse error: ${err instanceof Error ? err.message : String(err)}`,
    )
    return null
  }
  if (!isObject(parsed)) {
    acc.errors.push('YAML must contain an object')
    return null
  }
  const wfDefRaw = parsed['workflow_definition']
  if (!isObject(wfDefRaw)) {
    acc.errors.push('Missing "workflow_definition" key')
    return null
  }
  const stepsRaw = wfDefRaw['steps']
  if (!Array.isArray(stepsRaw)) {
    acc.errors.push('Missing or invalid "steps" array')
    return null
  }
  return stepsRaw as unknown[]
}

interface ValidationContext {
  autoIdCounter: number
  seenIds: Set<string>
  stepMap: Map<string, ValidatedStep>
  acc: ParseAccumulator
}

function validateStepsToMap(
  steps: unknown[],
  acc: ParseAccumulator,
): Map<string, ValidatedStep> {
  const ctx: ValidationContext = {
    autoIdCounter: 0,
    seenIds: new Set<string>(),
    stepMap: new Map<string, ValidatedStep>(),
    acc,
  }
  for (let i = 0; i < steps.length; i++) {
    validateOneStep(steps[i], i, ctx)
  }
  return ctx.stepMap
}

function validateOneStep(raw: unknown, i: number, ctx: ValidationContext): void {
  const step = narrowToYamlStep(raw, i, ctx.acc)
  if (!step) return
  const stepId = resolveStepId(step, i, ctx)
  if (!stepId) return
  const stepType = step.type ?? 'task'
  if (!VALID_TYPES.has(stepType)) {
    ctx.acc.errors.push(`Unknown step type "${stepType}" for step "${stepId}"`)
    return
  }
  ctx.seenIds.add(stepId)
  ctx.stepMap.set(stepId, { id: stepId, type: stepType, step, index: i })
}

function narrowToYamlStep(raw: unknown, i: number, acc: ParseAccumulator): YamlStep | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    acc.errors.push(`Step ${i + 1} is not an object (got ${describeRawType(raw)})`)
    return null
  }
  const step = raw as YamlStep
  if (step.id !== undefined && typeof step.id !== 'string') {
    acc.errors.push(`Step ${i + 1} has non-string id (got ${typeof step.id})`)
    return null
  }
  return step
}

function describeRawType(raw: unknown): string {
  if (raw === null) return 'null'
  if (Array.isArray(raw)) return 'array'
  return typeof raw
}

function resolveStepId(step: YamlStep, i: number, ctx: ValidationContext): string | null {
  const rawId = typeof step.id === 'string' ? step.id.trim() : ''
  const stepId = rawId || `auto-${++ctx.autoIdCounter}`
  if (!rawId) ctx.acc.warnings.push(`Step ${i + 1} has no id, auto-generated: ${stepId}`)
  if (RESERVED_IDS.has(stepId)) {
    ctx.acc.errors.push(`Step ${i + 1} uses reserved id "${stepId}"`)
    return null
  }
  if (ctx.seenIds.has(stepId)) {
    ctx.acc.errors.push(`Duplicate step id: ${stepId}`)
    return null
  }
  return stepId
}

function buildNodesFromStepMap(
  stepMap: Map<string, ValidatedStep>,
  totalSteps: number,
  existingPositions: Map<string, { x: number; y: number }> | undefined,
): Node[] {
  const nodes: Node[] = []
  nodes.push(buildSyntheticStartNode(existingPositions))
  for (const validated of stepMap.values()) {
    nodes.push(buildSingleStepNode(validated, existingPositions))
  }
  nodes.push(buildSyntheticEndNode(totalSteps, existingPositions))
  return nodes
}

function buildSyntheticStartNode(
  existingPositions: Map<string, { x: number; y: number }> | undefined,
): Node {
  const startId = 'start-1'
  return {
    id: startId,
    type: 'start',
    position: existingPositions?.get(startId) ?? { x: AUTO_LAYOUT_X, y: 50 },
    data: { label: 'Start', config: {} },
  }
}

function buildSingleStepNode(
  validated: ValidatedStep,
  existingPositions: Map<string, { x: number; y: number }> | undefined,
): Node {
  const position = existingPositions?.get(validated.id) ?? {
    x: AUTO_LAYOUT_X,
    y: AUTO_LAYOUT_Y_START + validated.index * AUTO_LAYOUT_Y_STEP,
  }
  return {
    id: validated.id,
    type: validated.type,
    position,
    data: {
      label: stringOrUndef(validated.step.title) ?? validated.id,
      config: buildStepConfig(validated.step, validated.type),
    },
  }
}

function buildSyntheticEndNode(
  totalSteps: number,
  existingPositions: Map<string, { x: number; y: number }> | undefined,
): Node {
  const endId = 'end-1'
  return {
    id: endId,
    type: 'end',
    position: existingPositions?.get(endId) ?? {
      x: AUTO_LAYOUT_X,
      y: AUTO_LAYOUT_Y_START + totalSteps * AUTO_LAYOUT_Y_STEP,
    },
    data: { label: 'End', config: {} },
  }
}
