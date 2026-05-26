/**
 * CodeMirror diff gutter extension for the Settings code editor.
 *
 * Shows colored markers on changed/added/removed lines relative
 * to the server version.
 */

import {
  type Extension,
  RangeSetBuilder,
  StateField,
  StateEffect,
} from '@codemirror/state'
import {
  EditorView,
  gutter,
  GutterMarker,
} from '@codemirror/view'

// ── Types ─────────────────────────────────────────────────────

export type LineDiffKind = 'changed' | 'added' | 'removed'

export interface LineDiff {
  /** 1-based line number in the edited document. */
  line: number
  kind: LineDiffKind
}

// ── LCS-based line diff ───────────────────────────────────────

/**
 * Simple line-by-line diff between the server text and the edited text.
 * Returns an array of diff markers for lines that differ.
 *
 * Removed lines are reported at the line number where the deletion
 * occurred in the edited document (clamped to the last line).
 */
/** Longest-common-subsequence length table for two line arrays. */
function buildLcsTable(serverLines: string[], editedLines: string[]): number[][] {
  const n = serverLines.length
  const m = editedLines.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i]![j] =
        serverLines[i - 1] === editedLines[j - 1]
          ? dp[i - 1]![j - 1]! + 1
          : Math.max(dp[i - 1]![j]!, dp[i]![j - 1]!)
    }
  }
  return dp
}

/** Backtrack the LCS table to the matched line indices on each side. */
function backtrackMatches(
  serverLines: string[],
  editedLines: string[],
  dp: number[][],
): { serverMatched: Set<number>; editedMatched: Set<number> } {
  let si = serverLines.length
  let ei = editedLines.length
  const serverMatched = new Set<number>()
  const editedMatched = new Set<number>()
  while (si > 0 && ei > 0) {
    if (serverLines[si - 1] === editedLines[ei - 1]) {
      serverMatched.add(si - 1)
      editedMatched.add(ei - 1)
      si--
      ei--
    } else if (dp[si - 1]![ei]! >= dp[si]![ei - 1]!) {
      si--
    } else {
      ei--
    }
  }
  return { serverMatched, editedMatched }
}

/** Classify unmatched lines: edited -> added, server -> removed. */
function collectDiffs(
  serverMatched: Set<number>,
  editedMatched: Set<number>,
  n: number,
  m: number,
): LineDiff[] {
  const diffs: LineDiff[] = []
  for (let j = 0; j < m; j++) {
    if (!editedMatched.has(j)) diffs.push({ line: j + 1, kind: 'added' })
  }
  // Removed server lines are shown at the nearest edited position.
  for (let i = 0; i < n; i++) {
    if (!serverMatched.has(i)) {
      diffs.push({ line: Math.max(1, Math.min(i + 1, m)), kind: 'removed' })
    }
  }
  return diffs.sort((a, b) => a.line - b.line)
}

export function computeLineDiff(serverText: string, editedText: string): LineDiff[] {
  if (serverText === editedText) return []
  const serverLines = serverText.replace(/\r\n/g, '\n').split('\n')
  const editedLines = editedText.replace(/\r\n/g, '\n').split('\n')

  // LCS-based diff identifies true additions, removals, and changes
  // (handles insertions/deletions at any position without cascading
  // false "changed" markers).
  const dp = buildLcsTable(serverLines, editedLines)
  const { serverMatched, editedMatched } = backtrackMatches(serverLines, editedLines, dp)
  return collectDiffs(serverMatched, editedMatched, serverLines.length, editedLines.length)
}

// ── Gutter markers ────────────────────────────────────────────

class DiffGutterMarker extends GutterMarker {
  constructor(readonly kind: LineDiffKind) {
    super()
  }

  override toDOM(): HTMLElement {
    const el = document.createElement('span')
    el.className = `cm-diff-marker cm-diff-marker-${this.kind}`
    el.setAttribute('aria-hidden', 'true')
    return el
  }
}

const changedMarker = new DiffGutterMarker('changed')
const addedMarker = new DiffGutterMarker('added')
const removedMarker = new DiffGutterMarker('removed')

function markerForKind(kind: LineDiffKind): DiffGutterMarker {
  switch (kind) {
    case 'changed': return changedMarker
    case 'added': return addedMarker
    case 'removed': return removedMarker
  }
}

// ── State effect + field ──────────────────────────────────────

const setDiffEffect = StateEffect.define<LineDiff[]>()

const diffField = StateField.define<LineDiff[]>({
  create: () => [],
  update(value, tr) {
    for (const effect of tr.effects) {
      if (effect.is(setDiffEffect)) return effect.value
    }
    return value
  },
})

/**
 * Dispatch new diff data into the editor.
 * Call this whenever serverText or editedText changes.
 */
export function dispatchDiff(
  view: EditorView,
  serverText: string,
  editedText: string,
): void {
  const diffs = computeLineDiff(serverText, editedText)
  view.dispatch({ effects: setDiffEffect.of(diffs) })
}

// ── Theme ─────────────────────────────────────────────────────

const diffGutterTheme = EditorView.theme({
  '.cm-diff-gutter': {
    width: '6px',
    marginRight: '2px',
  },
  '.cm-diff-marker': {
    display: 'inline-block',
    width: '4px',
    height: '100%',
    borderRadius: '1px',
  },
  '.cm-diff-marker-changed': {
    backgroundColor: 'var(--so-accent)',
  },
  '.cm-diff-marker-added': {
    backgroundColor: 'var(--so-success)',
  },
  '.cm-diff-marker-removed': {
    backgroundColor: 'var(--so-danger)',
  },
})

// ── Extension factory ─────────────────────────────────────────

/**
 * CodeMirror gutter extension that shows colored markers for
 * changed/added/removed lines relative to the server version.
 */
export function diffGutterExtension(): Extension {
  return [
    diffField,
    gutter({
      class: 'cm-diff-gutter',
      markers: (view) => {
        const diffs = view.state.field(diffField)
        const builder = new RangeSetBuilder<GutterMarker>()
        const doc = view.state.doc

        // diffs are already sorted by line from computeLineDiff
        const sorted = diffs
        const seen = new Set<number>()

        for (const diff of sorted) {
          // Clamp to valid line range
          const lineNum = Math.max(1, Math.min(diff.line, doc.lines))
          if (seen.has(lineNum)) continue
          seen.add(lineNum)
          const lineObj = doc.line(lineNum)
          builder.add(lineObj.from, lineObj.from, markerForKind(diff.kind))
        }

        return builder.finish()
      },
    }),
    diffGutterTheme,
  ]
}
