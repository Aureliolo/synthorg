/**
 * YAML editor for workflow definitions.
 *
 * Allows editing YAML directly and applying changes back to the visual canvas
 * via an explicit Apply action. Shows inline parse/validation errors.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check } from 'lucide-react'
import { LazyCodeMirrorEditor } from '@/components/ui/lazy-code-mirror-editor'
import { Button } from '@/components/ui/button'
import { useWorkflowEditorStore } from '@/stores/workflow-editor'
import { parseYamlToNodesEdges } from './yaml-to-nodes'
import type { Node } from '@xyflow/react'

const APPLIED_FLASH_MS = 2000

interface WorkflowYamlEditorProps {
  initialYaml: string
}

export function WorkflowYamlEditor({ initialYaml }: WorkflowYamlEditorProps) {
  const state = useYamlEditorState(initialYaml)

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1">
        <LazyCodeMirrorEditor
          value={state.yamlText}
          onChange={state.setYamlText}
          language="yaml"
        />
      </div>
      {state.parseErrors.length > 0 && <ParseErrorsBanner errors={state.parseErrors} />}
      {state.parseWarnings.length > 0 && state.parseErrors.length === 0 && (
        <ParseWarningsBanner warnings={state.parseWarnings} />
      )}
      <div className="flex items-center justify-end gap-2 border-t border-border p-2">
        {state.applied && (
          <span className="flex items-center gap-1 text-xs text-success">
            <Check className="size-3" />
            Applied
          </span>
        )}
        <Button variant="outline" size="sm" onClick={state.handleRevert}>
          Revert
        </Button>
        <Button size="sm" onClick={state.handleApply}>
          Apply to Canvas
        </Button>
      </div>
    </div>
  )
}

interface YamlEditorState {
  yamlText: string
  parseErrors: string[]
  parseWarnings: string[]
  applied: boolean
  setYamlText: (value: string) => void
  handleApply: () => void
  handleRevert: () => void
}

function useYamlEditorState(initialYaml: string): YamlEditorState {
  const [yamlText, setYamlText] = useState(initialYaml)
  const [parseErrors, setParseErrors] = useState<string[]>([])
  const [parseWarnings, setParseWarnings] = useState<string[]>([])
  const [applied, setApplied] = useState(false)
  const appliedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (appliedTimerRef.current !== null) clearTimeout(appliedTimerRef.current)
    }
  }, [])

  /* eslint-disable @eslint-react/set-state-in-effect -- intentional prop-to-state sync on external change */
  useEffect(() => {
    setYamlText(initialYaml)
    setParseErrors([])
    setParseWarnings([])
    setApplied(false)
    if (appliedTimerRef.current !== null) {
      clearTimeout(appliedTimerRef.current)
      appliedTimerRef.current = null
    }
  }, [initialYaml])
  /* eslint-enable @eslint-react/set-state-in-effect */

  const handleApply = useCallback(
    () => applyYamlToCanvas({ yamlText, setParseErrors, setParseWarnings, setApplied, appliedTimerRef }),
    [yamlText],
  )

  const handleRevert = useCallback(() => {
    setYamlText(initialYaml)
    setParseErrors([])
    setParseWarnings([])
  }, [initialYaml])

  return {
    yamlText,
    parseErrors,
    parseWarnings,
    applied,
    setYamlText,
    handleApply,
    handleRevert,
  }
}

interface ApplyYamlArgs {
  yamlText: string
  setParseErrors: (v: string[]) => void
  setParseWarnings: (v: string[]) => void
  setApplied: (v: boolean) => void
  appliedTimerRef: { current: ReturnType<typeof setTimeout> | null }
}

function applyYamlToCanvas(args: ApplyYamlArgs): void {
  const positionMap = buildPositionMap()
  const result = parseYamlToNodesEdges(args.yamlText, positionMap)
  args.setParseErrors(result.errors)
  args.setParseWarnings(result.warnings)
  if (result.errors.length > 0) return
  const store = useWorkflowEditorStore.getState()
  if (!store.definition) return
  applyParsedResultToStore(args.yamlText, result.nodes, result.edges)
  args.setApplied(true)
  scheduleAppliedReset(args.appliedTimerRef, () => args.setApplied(false))
}

function buildPositionMap(): Map<string, { x: number; y: number }> {
  const positionMap = new Map<string, { x: number; y: number }>()
  for (const node of useWorkflowEditorStore.getState().nodes) {
    positionMap.set(node.id, node.position)
  }
  return positionMap
}

function applyParsedResultToStore(
  yamlText: string,
  parsedNodes: ReturnType<typeof parseYamlToNodesEdges>['nodes'],
  edges: ReturnType<typeof parseYamlToNodesEdges>['edges'],
): void {
  const store = useWorkflowEditorStore.getState()
  const mappedNodes: Node[] = parsedNodes.map((n) => ({
    ...n,
    data: {
      ...n.data,
      label: typeof n.data.label === 'string' ? n.data.label : n.id,
    },
  }))
  const snapshot = {
    nodes: structuredClone(store.nodes),
    edges: structuredClone(store.edges),
  }
  useWorkflowEditorStore.setState((s) => ({
    nodes: mappedNodes,
    edges,
    selectedNodeId: null,
    dirty: true,
    yamlPreview: yamlText,
    undoStack: [...s.undoStack.slice(-49), snapshot],
    redoStack: [],
  }))
}

function scheduleAppliedReset(
  timerRef: { current: ReturnType<typeof setTimeout> | null },
  reset: () => void,
): void {
  if (timerRef.current !== null) clearTimeout(timerRef.current)
  timerRef.current = setTimeout(() => {
    reset()
    timerRef.current = null
  }, APPLIED_FLASH_MS)
}

interface ParseErrorsBannerProps {
  errors: readonly string[]
}

function ParseErrorsBanner({ errors }: ParseErrorsBannerProps) {
  return (
    <div className="border-t border-danger/30 bg-danger/5 p-card">
      <div className="flex items-center gap-1.5 text-sm font-medium text-danger">
        <AlertTriangle className="size-4" />
        {errors.length} error{errors.length !== 1 ? 's' : ''}
      </div>
      <ul className="mt-1 space-y-0.5 text-xs text-danger">
        {errors.map((err, index) => (
          // eslint-disable-next-line @eslint-react/no-array-index-key -- error messages may duplicate; index disambiguates
          <li key={`yaml-error-${index}`}>{err}</li>
        ))}
      </ul>
    </div>
  )
}

interface ParseWarningsBannerProps {
  warnings: readonly string[]
}

function ParseWarningsBanner({ warnings }: ParseWarningsBannerProps) {
  return (
    <div className="border-t border-warning/30 bg-warning/5 p-card">
      <ul className="space-y-0.5 text-xs text-warning">
        {warnings.map((w, index) => (
          // eslint-disable-next-line @eslint-react/no-array-index-key -- warning messages may duplicate; index disambiguates
          <li key={`yaml-warning-${index}`}>{w}</li>
        ))}
      </ul>
    </div>
  )
}
