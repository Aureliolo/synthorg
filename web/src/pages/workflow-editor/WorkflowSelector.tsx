import { useEffect, useMemo } from 'react'
import { SelectField } from '@/components/ui/select-field'
import type { SelectOption } from '@/components/ui/select-field'
import { useWorkflowsStore } from '@/stores/workflows'

interface WorkflowSelectorProps {
  currentId: string | null
  onChange: (id: string) => void
}

export function WorkflowSelector({ currentId, onChange }: WorkflowSelectorProps) {
  const workflows = useWorkflowsStore((s) => s.workflows)
  const loading = useWorkflowsStore((s) => s.listLoading)
  const fetchWorkflows = useWorkflowsStore((s) => s.fetchWorkflows)

  useEffect(() => {
    void fetchWorkflows()
  }, [fetchWorkflows])

  const options: readonly SelectOption[] = useMemo(
    () => workflows.map((w) => ({ value: w.id, label: w.name })),
    [workflows],
  )

  const isLoadingEmpty = loading && workflows.length === 0

  return (
    <SelectField
      label="Select workflow"
      options={isLoadingEmpty ? [] : options}
      value={currentId ?? ''}
      onChange={(value) => {
        if (value) onChange(value)
      }}
      disabled={isLoadingEmpty}
      placeholder={isLoadingEmpty ? 'Loading...' : 'Select workflow'}
      className="h-7 w-44 truncate text-xs"
    />
  )
}
