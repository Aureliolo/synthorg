/**
 * Structured condition expression types for the workflow editor.
 *
 * Supports simple comparisons (key == value, key != value) and compound
 * expressions joined by AND/OR logical operators. Compatible with the
 * backend condition evaluator.
 */

export type ComparisonOperator = '==' | '!='

export type LogicalOperator = 'AND' | 'OR' | 'NOT'

export interface ConditionComparison {
  readonly kind: 'comparison'
  field: string
  operator: ComparisonOperator
  value: string
}

export interface ConditionGroup {
  readonly kind: 'group'
  logicalOperator: LogicalOperator
  conditions: ConditionExpression[]
}

export type ConditionExpression = ConditionComparison | ConditionGroup

/** Create a new comparison with default values. */
export function createComparison(
  field = 'status',
  operator: ComparisonOperator = '==',
  value = 'completed',
): ConditionComparison {
  return { kind: 'comparison', field, operator, value }
}

/** Create a new group with default values. */
export function createGroup(
  logicalOperator: LogicalOperator = 'AND',
  conditions: ConditionExpression[] = [],
): ConditionGroup {
  return { kind: 'group', logicalOperator, conditions }
}

/**
 * Serialize a structured condition expression to the string format
 * expected by the backend condition evaluator.
 *
 * Single comparison: "field == value"
 * Compound: "field1 == val1 AND field2 != val2"
 * Nested groups are wrapped in parentheses:
 *   "(field1 == val1 AND field2 != val2) OR field3 == val3"
 */
export function serializeCondition(expr: ConditionExpression): string {
  if (expr.kind === 'comparison') {
    return `${expr.field} ${expr.operator} ${expr.value}`
  }

  if (expr.conditions.length === 0) return ''

  // NOT wraps its single child in parentheses
  if (expr.logicalOperator === 'NOT') {
    const inner = expr.conditions.map((c) => serializeCondition(c)).join(' AND ')
    return `NOT (${inner})`
  }

  const parts = expr.conditions.map((c) => {
    // Nested groups get parenthesized to preserve precedence
    if (c.kind === 'group' && c.conditions.length > 1) {
      return `(${serializeCondition(c)})`
    }
    return serializeCondition(c)
  })

  return parts.join(` ${expr.logicalOperator} `)
}

/**
 * Parse a single comparison token: "field == value" or "field != value".
 * Returns null if the string cannot be parsed as a simple comparison.
 */
function parseSingleComparison(str: string): ConditionComparison | null {
  const trimmed = str.trim()
  if (!trimmed) return null

  const match = trimmed.match(/^(\S+)\s+(==|!=)\s+(.+)$/)
  if (!match) return null

  return createComparison(
    match[1],
    match[2] as ComparisonOperator,
    match[3]!.trim(),
  )
}

/**
 * Split a condition string by a logical operator, respecting parenthesized
 * groups. Returns null if the operator is not found at the top level.
 */
function splitByOperator(str: string, op: LogicalOperator): string[] | null {
  const tokenRegex = new RegExp(`\\s+${op}\\s+`) // eslint-disable-line security/detect-non-literal-regexp -- op is from LogicalOperator literal union
  const state = { parts: [] as string[], depth: 0, current: '', i: 0 }
  while (state.i < str.length) {
    const handled = consumeSplitChar(str, state, tokenRegex)
    if (handled === 'unbalanced') return null
  }
  if (state.current.trim()) state.parts.push(state.current.trim())
  if (state.depth !== 0) return null // Unbalanced parens
  return state.parts.length > 1 ? state.parts : null
}

interface SplitState {
  parts: string[]
  depth: number
  current: string
  i: number
}

function consumeSplitChar(
  str: string,
  state: SplitState,
  tokenRegex: RegExp,
): 'ok' | 'unbalanced' {
  const ch = str[state.i]
  if (ch === '(') {
    state.depth++
    state.current += '('
    state.i++
    return 'ok'
  }
  if (ch === ')') {
    state.depth--
    if (state.depth < 0) return 'unbalanced'
    state.current += ')'
    state.i++
    return 'ok'
  }
  if (state.depth === 0) {
    const match = str.substring(state.i).match(tokenRegex)
    if (match?.index === 0) {
      state.parts.push(state.current.trim())
      state.current = ''
      state.i += match[0].length
      return 'ok'
    }
  }
  state.current += ch
  state.i++
  return 'ok'
}

/**
 * Unwrap a single layer of balanced parentheses from a string.
 * "(foo == bar)" -> "foo == bar"
 * "(a == 1 AND b == 2)" -> "a == 1 AND b == 2"
 */
function unwrapParens(str: string): string {
  const trimmed = str.trim()
  if (!trimmed.startsWith('(') || !trimmed.endsWith(')')) return trimmed

  // Check if the outer parens are balanced as a pair
  let depth = 0
  for (let i = 0; i < trimmed.length - 1; i++) {
    if (trimmed[i] === '(') depth++
    else if (trimmed[i] === ')') depth--
    if (depth === 0) return trimmed // Inner close before end -- not a wrapping pair
  }

  return trimmed.slice(1, -1).trim()
}

/** Discriminated result for the speculative ``tryParse*`` helpers. The
 * three states are distinct: ``no_match`` means the input did not look like
 * the construct this helper handles (try the next one); ``error`` means it
 * looked right but parsing failed (give up); ``ok`` carries the parsed
 * expression. Avoids the older ``ConditionExpression | null | undefined``
 * shape where callers had to recall which sentinel meant what. */
type TryParseResult =
  | { kind: 'no_match' }
  | { kind: 'error' }
  | { kind: 'ok'; expr: ConditionExpression }

/**
 * Parse a condition string into a structured expression.
 * Supports:
 * - Simple: "field == value"
 * - Compound: "field1 == val1 AND field2 != val2"
 * - Mixed: "field1 == val1 OR field2 != val2"
 * - Parenthesized groups: "(a == 1 AND b == 2) OR c == 3"
 *
 * Returns null if the string cannot be parsed.
 */
function parseConditionString(str: string): ConditionExpression | null {
  const trimmed = str.trim()
  if (!trimmed) return null
  const negation = tryParseNotPrefix(trimmed)
  if (negation.kind === 'ok') return negation.expr
  if (negation.kind === 'error') return null
  const unwrapped = unwrapParens(trimmed)
  // Split by OR first (lower precedence), then AND (higher precedence).
  for (const op of ['OR', 'AND'] as const) {
    const grouped = tryParseLogicalGroup(unwrapped, op)
    if (grouped.kind === 'ok') return grouped.expr
    if (grouped.kind === 'error') return null
  }
  return parseSingleComparison(unwrapped)
}

function tryParseNotPrefix(trimmed: string): TryParseResult {
  const notMatch = /^NOT\s*\((.+)\)\s*$/i.exec(trimmed)
  if (!notMatch?.[1]) return { kind: 'no_match' }
  const inner = parseConditionString(notMatch[1])
  if (!inner) return { kind: 'error' }
  return { kind: 'ok', expr: createGroup('NOT', [inner]) }
}

function tryParseLogicalGroup(
  unwrapped: string,
  op: 'AND' | 'OR',
): TryParseResult {
  const parts = splitByOperator(unwrapped, op)
  if (!parts) return { kind: 'no_match' }
  const conditions: ConditionExpression[] = []
  for (const part of parts) {
    const parsed = parseConditionString(part)
    if (!parsed) return { kind: 'error' }
    conditions.push(parsed)
  }
  return { kind: 'ok', expr: createGroup(op, conditions) }
}

/** Extended builder state including negate and sub-groups. */
export interface BuilderState {
  comparisons: ConditionComparison[]
  logicalOperator: 'AND' | 'OR'
  negate: boolean
  subGroups: { operator: 'AND' | 'OR'; comparisons: ConditionComparison[] }[]
}

/**
 * Parse a condition string into full builder state, supporting NOT
 * wrappers and one level of nested groups.  Returns null only for
 * expressions too deep for the builder UI.
 */
export function parseForBuilderState(str: string): BuilderState | null {
  const parsed = parseConditionString(str)
  if (!parsed) return null
  const { expr, negate } = unwrapNotPrefix(parsed)
  if (expr.kind === 'comparison') {
    return { comparisons: [expr], logicalOperator: 'AND', negate, subGroups: [] }
  }
  const groupRows = partitionGroupChildren(expr.conditions)
  if (!groupRows) return null
  if (groupRows.comparisons.length === 0 && groupRows.subGroups.length === 0) return null
  const op = (expr.logicalOperator === 'NOT' ? 'AND' : expr.logicalOperator)
  return {
    comparisons: groupRows.comparisons,
    logicalOperator: op,
    negate,
    subGroups: groupRows.subGroups,
  }
}

function unwrapNotPrefix(
  expr: ConditionExpression,
): { expr: ConditionExpression; negate: boolean } {
  if (
    expr.kind === 'group' &&
    expr.logicalOperator === 'NOT' &&
    expr.conditions.length === 1
  ) {
    return { expr: expr.conditions[0]!, negate: true }
  }
  return { expr, negate: false }
}

function partitionGroupChildren(
  children: readonly ConditionExpression[],
): { comparisons: ConditionComparison[]; subGroups: BuilderState['subGroups'] } | null {
  const comparisons: ConditionComparison[] = []
  const subGroups: BuilderState['subGroups'] = []
  for (const child of children) {
    if (child.kind === 'comparison') {
      comparisons.push(child)
      continue
    }
    if (child.kind === 'group' && child.logicalOperator !== 'NOT') {
      const sub = collectFlatSubGroup(child)
      if (!sub) return null
      subGroups.push(sub)
      continue
    }
    return null // can't handle NOT sub-groups or deeper nesting
  }
  return { comparisons, subGroups }
}

function collectFlatSubGroup(
  group: ConditionExpression & { kind: 'group' },
): BuilderState['subGroups'][number] | null {
  const groupComparisons: ConditionComparison[] = []
  for (const gc of group.conditions) {
    if (gc.kind !== 'comparison') return null // nesting too deep
    groupComparisons.push(gc)
  }
  return {
    operator: group.logicalOperator as 'AND' | 'OR',
    comparisons: groupComparisons,
  }
}

/** Common field suggestions for the condition builder. */
export const CONDITION_FIELDS = [
  'status',
  'priority',
  'task.status',
  'task.priority',
  'task.type',
  'approved',
  'env',
] as const

/** Common comparison values. */
export const CONDITION_VALUES = [
  'true',
  'false',
  'completed',
  'failed',
  'high',
  'medium',
  'low',
  'critical',
  'approved',
  'rejected',
] as const
