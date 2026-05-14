/**
 * Per-node-type configuration field definitions for the property drawer.
 */

import type { WorkflowNodeType } from '@/api/types/workflows'

export interface ConfigField {
  key: string
  label: string
  type: 'text' | 'select' | 'number'
  options?: readonly { value: string; label: string }[]
  placeholder?: string
  required?: boolean
}

const PRIORITY_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'critical', label: 'Critical' },
] as const

const COMPLEXITY_OPTIONS = [
  { value: 'simple', label: 'Simple' },
  { value: 'medium', label: 'Medium' },
  { value: 'complex', label: 'Complex' },
  { value: 'epic', label: 'Epic' },
] as const

const TASK_TYPE_OPTIONS = [
  { value: 'development', label: 'Development' },
  { value: 'design', label: 'Design' },
  { value: 'research', label: 'Research' },
  { value: 'review', label: 'Review' },
  { value: 'meeting', label: 'Meeting' },
  { value: 'admin', label: 'Admin' },
] as const

const ROUTING_STRATEGY_OPTIONS = [
  { value: 'manual', label: 'Manual' },
  { value: 'role_based', label: 'Role-based' },
  { value: 'load_balanced', label: 'Load-balanced' },
  { value: 'auction', label: 'Auction' },
  { value: 'hierarchical', label: 'Hierarchical' },
  { value: 'cost_optimized', label: 'Cost-optimized' },
] as const

const TOPOLOGY_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'sas', label: 'Single-Agent (SAS)' },
  { value: 'centralized', label: 'Centralized' },
  { value: 'decentralized', label: 'Decentralized' },
  { value: 'context_dependent', label: 'Context-dependent' },
] as const

const JOIN_STRATEGY_OPTIONS = [
  { value: 'all', label: 'Wait for All' },
  { value: 'any', label: 'Wait for Any' },
] as const

export const NODE_CONFIG_SCHEMAS: Record<WorkflowNodeType, readonly ConfigField[]> = {
  start: [
    { key: 'label', label: 'Label', type: 'text', placeholder: 'Start' },
  ],
  end: [
    { key: 'label', label: 'Label', type: 'text', placeholder: 'End' },
  ],
  task: [
    { key: 'title', label: 'Title', type: 'text', required: true, placeholder: 'Task title' },
    { key: 'task_type', label: 'Type', type: 'select', options: TASK_TYPE_OPTIONS },
    { key: 'priority', label: 'Priority', type: 'select', options: PRIORITY_OPTIONS },
    { key: 'complexity', label: 'Complexity', type: 'select', options: COMPLEXITY_OPTIONS },
    { key: 'coordination_topology', label: 'Topology', type: 'select', options: TOPOLOGY_OPTIONS },
  ],
  agent_assignment: [
    { key: 'routing_strategy', label: 'Strategy', type: 'select', options: ROUTING_STRATEGY_OPTIONS },
    { key: 'role_filter', label: 'Role Filter', type: 'text', placeholder: 'e.g. frontend_developer' },
    { key: 'agent_name', label: 'Agent Name', type: 'text', placeholder: 'Specific agent (optional)' },
  ],
  conditional: [
    { key: 'condition_expression', label: 'Condition', type: 'text', required: true, placeholder: 'e.g. status == approved' },
  ],
  parallel_split: [
    { key: 'max_concurrency', label: 'Max Concurrency', type: 'number', placeholder: 'Unlimited' },
  ],
  parallel_join: [
    { key: 'join_strategy', label: 'Join Strategy', type: 'select', options: JOIN_STRATEGY_OPTIONS },
  ],
  subworkflow: [
    {
      key: 'subworkflow_id',
      label: 'Subworkflow ID',
      type: 'text',
      required: true,
      placeholder: 'sub-...',
    },
    {
      key: 'version',
      label: 'Pinned version',
      type: 'text',
      required: true,
      placeholder: 'e.g. 1.0.0',
    },
    {
      key: 'input_bindings',
      label: 'Input Bindings (JSON)',
      type: 'text',
      placeholder: '{"quarter": "@parent.current_quarter"}',
    },
    {
      key: 'output_bindings',
      label: 'Output Bindings (JSON)',
      type: 'text',
      placeholder: '{"report": "@child.closing_report"}',
    },
  ],
  verification: [
    { key: 'label', label: 'Label', type: 'text', placeholder: 'Verify' },
  ],
}
