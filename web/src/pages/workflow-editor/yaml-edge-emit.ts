/**
 * Edge emission helpers used by `parseYamlToNodesEdges`. Splits the work into
 * per-source-kind emitters (depends_on entries and parallel_split branches)
 * plus the synthetic start/end connectors.
 */
import type { Edge } from '@xyflow/react'
import type { WorkflowEdgeType } from '@/api/types/workflows'
import { isObject } from '@/utils/type-guards'

export interface ValidatedStep {
  id: string
  type: string
  step: {
    type?: string
    condition?: string
    depends_on?: (string | number | { id: string; branch?: string })[]
    branches?: string[]
  }
  index: number
}

export interface ParseAccumulator {
  errors: string[]
  warnings: string[]
}

export interface EdgeEmitContext {
  conditionalBranchCounters: Map<string, number>
  emittedEdges: Set<string>
  stepMap: Map<string, ValidatedStep>
  seenIds: Set<string>
  edges: Edge[]
  acc: ParseAccumulator
}

export function emitEdgesFromStepMap(
  stepMap: Map<string, ValidatedStep>,
  acc: ParseAccumulator,
): Edge[] {
  const ctx: EdgeEmitContext = {
    conditionalBranchCounters: new Map(),
    emittedEdges: new Set(),
    stepMap,
    seenIds: new Set(stepMap.keys()),
    edges: [],
    acc,
  }
  for (const validated of stepMap.values()) {
    emitDependsOnEdges(validated, ctx)
    emitBranchesEdges(validated, ctx)
  }
  return ctx.edges
}

function emitDependsOnEdges(validated: ValidatedStep, ctx: EdgeEmitContext): void {
  const { step, id: stepId } = validated
  if (step.depends_on === undefined) return
  if (!Array.isArray(step.depends_on)) {
    ctx.acc.errors.push(
      `Step '${stepId}' has non-array depends_on (got ${typeof step.depends_on})`,
    )
    return
  }
  for (const rawDep of step.depends_on) {
    emitOneDependency(stepId, rawDep, ctx)
  }
}

function emitOneDependency(
  stepId: string,
  rawDep: string | number | { id: string; branch?: string },
  ctx: EdgeEmitContext,
): void {
  const parsedDep = parseDependsOnEntry(stepId, rawDep, ctx.acc)
  if (!parsedDep) return
  const { depId, explicitBranch } = parsedDep
  if (!ctx.seenIds.has(depId)) {
    ctx.acc.errors.push(`Step '${stepId}' references unknown dependency '${depId}'`)
    return
  }
  const sourceStep = ctx.stepMap.get(depId)!
  const edgeType = resolveDependsOnEdgeType({
    sourceStep,
    explicitBranch,
    conditionalBranchCounters: ctx.conditionalBranchCounters,
    depId,
    stepId,
    acc: ctx.acc,
  })
  pushUniqueEdge(ctx, depId, stepId, edgeType)
}

interface ParsedDep {
  depId: string
  explicitBranch: 'true' | 'false' | undefined
}

function parseDependsOnEntry(
  stepId: string,
  rawDep: unknown,
  acc: ParseAccumulator,
): ParsedDep | null {
  if (isObject(rawDep) && 'id' in rawDep) {
    return parseObjectDependsOnEntry(stepId, rawDep, acc)
  }
  if (typeof rawDep === 'string' || typeof rawDep === 'number') {
    return parsePrimitiveDependsOnEntry(stepId, rawDep, acc)
  }
  acc.errors.push(`Step '${stepId}' has invalid dependency: ${JSON.stringify(rawDep)}`)
  return null
}

function parseObjectDependsOnEntry(
  stepId: string,
  rawDep: Record<string, unknown>,
  acc: ParseAccumulator,
): ParsedDep | null {
  const depId = String(rawDep.id ?? '').trim()
  if (!depId) {
    acc.errors.push(`Step '${stepId}' has empty dependency`)
    return null
  }
  const branch = rawDep.branch !== undefined ? String(rawDep.branch) : undefined
  if (branch === 'true' || branch === 'false') {
    return { depId, explicitBranch: branch }
  }
  if (branch !== undefined) {
    acc.warnings.push(
      `Step '${stepId}' dependency '${depId}' has unrecognized branch value '${branch}': falling back to inference`,
    )
  }
  return { depId, explicitBranch: undefined }
}

function parsePrimitiveDependsOnEntry(
  stepId: string,
  rawDep: string | number,
  acc: ParseAccumulator,
): ParsedDep | null {
  const depId = String(rawDep).trim()
  if (!depId) {
    acc.errors.push(`Step '${stepId}' has empty dependency`)
    return null
  }
  return { depId, explicitBranch: undefined }
}

interface EdgeTypeArgs {
  sourceStep: ValidatedStep
  explicitBranch: 'true' | 'false' | undefined
  conditionalBranchCounters: Map<string, number>
  depId: string
  stepId: string
  acc: ParseAccumulator
}

function resolveDependsOnEdgeType(args: EdgeTypeArgs): WorkflowEdgeType {
  if (args.explicitBranch !== undefined) return resolveExplicitBranchEdgeType(args)
  return resolveImplicitBranchEdgeType(args)
}

function resolveExplicitBranchEdgeType(args: EdgeTypeArgs): WorkflowEdgeType {
  const { sourceStep, explicitBranch, conditionalBranchCounters, depId, stepId, acc } = args
  if (sourceStep.type !== 'conditional') {
    acc.warnings.push(
      `Step '${stepId}': explicit branch '${explicitBranch}' on non-conditional dependency '${depId}'`,
    )
  }
  // Only advance the counter when the true slot is consumed so a subsequent
  // implicit entry correctly gets the false slot.
  if (
    sourceStep.type === 'conditional' &&
    sourceStep.step.condition &&
    explicitBranch === 'true'
  ) {
    const branchIdx = conditionalBranchCounters.get(depId) ?? 0
    conditionalBranchCounters.set(depId, branchIdx + 1)
  }
  return explicitBranch === 'true' ? 'conditional_true' : 'conditional_false'
}

function resolveImplicitBranchEdgeType(args: EdgeTypeArgs): WorkflowEdgeType {
  const branchIdx = args.conditionalBranchCounters.get(args.depId) ?? 0
  const edgeType = inferDependsOnEdgeType(args.sourceStep, branchIdx)
  if (args.sourceStep.type === 'conditional' && args.sourceStep.step.condition) {
    args.conditionalBranchCounters.set(args.depId, branchIdx + 1)
  }
  return edgeType
}

function pushUniqueEdge(
  ctx: EdgeEmitContext,
  source: string,
  target: string,
  edgeType: WorkflowEdgeType,
): void {
  const edgeKey = `${source}->${target}:${edgeType}`
  if (ctx.emittedEdges.has(edgeKey)) return
  ctx.emittedEdges.add(edgeKey)
  const visualType = edgeTypeToVisualType(edgeType)
  const isTrue = edgeType === 'conditional_true'
  const isFalse = edgeType === 'conditional_false'
  ctx.edges.push({
    id: `edge-${source}-${target}-${edgeType}`,
    source,
    target,
    type: visualType,
    sourceHandle: isTrue ? 'true' : isFalse ? 'false' : undefined,
    data: {
      edgeType,
      branch: isTrue ? 'true' : isFalse ? 'false' : undefined,
    },
  })
}

function emitBranchesEdges(validated: ValidatedStep, ctx: EdgeEmitContext): void {
  const { step, id: stepId } = validated
  if (step.branches === undefined) return
  if (!Array.isArray(step.branches)) {
    ctx.acc.errors.push(
      `Step '${stepId}' has non-array branches (got ${typeof step.branches})`,
    )
    return
  }
  for (const rawTarget of step.branches) {
    emitOneBranch(stepId, rawTarget, ctx)
  }
}

function emitOneBranch(stepId: string, rawTarget: unknown, ctx: EdgeEmitContext): void {
  if (typeof rawTarget !== 'string' && typeof rawTarget !== 'number') {
    ctx.acc.errors.push(
      `Step '${stepId}' has non-string branch target: ${JSON.stringify(rawTarget)}`,
    )
    return
  }
  const branchTarget = String(rawTarget).trim()
  if (!branchTarget) {
    ctx.acc.errors.push(`Step '${stepId}' has empty branch target`)
    return
  }
  if (!ctx.seenIds.has(branchTarget)) {
    ctx.acc.errors.push(
      `Step '${stepId}' references unknown branch target '${branchTarget}'`,
    )
    return
  }
  pushUniqueEdge(ctx, stepId, branchTarget, 'parallel_branch')
}

export function connectStartAndEnd(
  stepMap: Map<string, ValidatedStep>,
  edges: Edge[],
): void {
  const startId = 'start-1'
  const endId = 'end-1'
  const stepIds = [...stepMap.keys()]
  const hasIncoming = new Set(edges.map((e) => e.target))
  for (const id of stepIds) {
    if (!hasIncoming.has(id)) {
      edges.push({
        id: `edge-${startId}-${id}`,
        source: startId,
        target: id,
        type: 'sequential',
        data: { edgeType: 'sequential' },
      })
    }
  }
  const hasOutgoing = new Set(edges.map((e) => e.source))
  for (const id of stepIds) {
    if (!hasOutgoing.has(id)) {
      edges.push({
        id: `edge-${id}-${endId}`,
        source: id,
        target: endId,
        type: 'sequential',
        data: { edgeType: 'sequential' },
      })
    }
  }
}

function edgeTypeToVisualType(edgeType: WorkflowEdgeType): string {
  if (edgeType === 'conditional_true' || edgeType === 'conditional_false') {
    return 'conditional'
  }
  return edgeType
}

function inferDependsOnEdgeType(
  sourceStep: ValidatedStep,
  branchIndex: number,
): WorkflowEdgeType {
  if (sourceStep.type === 'conditional' && sourceStep.step.condition) {
    return branchIndex === 0 ? 'conditional_true' : 'conditional_false'
  }
  return 'sequential'
}
