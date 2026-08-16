import { useEffect, useMemo } from 'react'

import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { useAgentsStore } from '@/stores/agents'
import { UNASSIGNED_LABEL, UNKNOWN_AGENT_NAME } from '@/utils/agents'

export interface AssigneeSelectProps {
  id: string
  /** The current assignee's id, or ``null`` when nobody holds the task. */
  value: string | null
  /** The current assignee's resolved name, when the backend found one. */
  valueName: string | null
  onChange: (assignedTo: string | null) => Promise<void> | void
}

/**
 * Reassign a task by picking the agent, never by typing their identifier.
 *
 * The roster is fetched here because choosing between agents needs the list
 * of them; that is a different question from "what is this one called", which
 * the backend already answered on the row. The option's value is still the
 * id, since that is what the API assigns by, but no id reaches the operator.
 *
 * An assignee the roster does not cover (retired, or from another
 * organisation) keeps its own option so the control shows the current state
 * rather than silently reading as unassigned.
 */
export function AssigneeSelect({ id, value, valueName, onChange }: AssigneeSelectProps) {
  const agents = useAgentsStore((s) => s.agents)
  const fetchAgents = useAgentsStore((s) => s.fetchAgents)

  useEffect(() => {
    if (agents.length === 0) void fetchAgents()
  }, [agents.length, fetchAgents])

  const options = useMemo<readonly SelectOption[]>(() => {
    const roster = agents.map((a) => ({ value: a.id, label: a.name }))
    if (value && !roster.some((o) => o.value === value)) {
      return [{ value, label: valueName ?? UNKNOWN_AGENT_NAME }, ...roster]
    }
    return roster
  }, [agents, value, valueName])

  return (
    <SelectField
      label="Assignee"
      hideLabel
      options={options}
      value={value ?? ''}
      placeholder={UNASSIGNED_LABEL}
      staleValueLabel={valueName ?? UNKNOWN_AGENT_NAME}
      onChange={(next) => void onChange(next || null)}
      className="text-sm"
      describedBy={id}
    />
  )
}
