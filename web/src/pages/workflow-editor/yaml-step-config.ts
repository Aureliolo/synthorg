/**
 * Per-node-kind config builders for YAML -> nodes conversion. Each builder
 * mutates a config Record<string, unknown> for the matching step type.
 */

export interface YamlStepConfigInput {
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
}

type ConfigFiller = (
  step: YamlStepConfigInput,
  config: Record<string, unknown>,
) => void

const CONFIG_FILLERS: Readonly<Record<string, ConfigFiller>> = {
  task: fillTaskConfig,
  conditional: fillConditionalConfig,
  parallel_split: fillParallelSplitConfig,
  parallel_join: fillParallelJoinConfig,
  agent_assignment: fillAgentAssignmentConfig,
  subworkflow: fillSubworkflowConfig,
}

export function buildStepConfig(
  step: YamlStepConfigInput,
  stepType: string,
): Record<string, unknown> {
  const config: Record<string, unknown> = {}
  CONFIG_FILLERS[stepType]?.(step, config)
  return config
}

export function stringOrUndef(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function fillTaskConfig(
  step: YamlStepConfigInput,
  config: Record<string, unknown>,
): void {
  const fields: readonly (keyof YamlStepConfigInput)[] = [
    'title',
    'task_type',
    'priority',
    'complexity',
    'coordination_topology',
  ]
  for (const key of fields) {
    const value = stringOrUndef(step[key])
    if (value) config[key] = value
  }
}

function fillConditionalConfig(
  step: YamlStepConfigInput,
  config: Record<string, unknown>,
): void {
  const expr = stringOrUndef(step.condition)
  if (expr) config['condition_expression'] = expr
}

function fillParallelSplitConfig(
  step: YamlStepConfigInput,
  config: Record<string, unknown>,
): void {
  if (typeof step.max_concurrency === 'number') {
    config['max_concurrency'] = step.max_concurrency
  }
}

function fillParallelJoinConfig(
  step: YamlStepConfigInput,
  config: Record<string, unknown>,
): void {
  config['join_strategy'] = stringOrUndef(step.join_strategy) ?? 'all'
}

function fillAgentAssignmentConfig(
  step: YamlStepConfigInput,
  config: Record<string, unknown>,
): void {
  const strategy = stringOrUndef(step.strategy)
  if (strategy) config['routing_strategy'] = strategy
  const role = stringOrUndef(step.role)
  if (role) config['role_filter'] = role
  const agentName = stringOrUndef(step.agent_name)
  if (agentName) config['agent_name'] = agentName
}

function fillSubworkflowConfig(
  step: YamlStepConfigInput,
  config: Record<string, unknown>,
): void {
  const subworkflowId = stringOrUndef(step.subworkflow_id)
  if (subworkflowId) config['subworkflow_id'] = subworkflowId
  const version = stringOrUndef(step.version)
  if (version) config['version'] = version
  if (isPlainObject(step.input_bindings)) {
    config['input_bindings'] = step.input_bindings
  }
  if (isPlainObject(step.output_bindings)) {
    config['output_bindings'] = step.output_bindings
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
