import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import type { SelectOption } from '@/components/ui/select-field'
import { useWorkflowsStore } from '@/stores/workflows'
import { WORKFLOW_TYPES } from '@/utils/constants'
import { formatLabel } from '@/utils/format'

const WORKFLOW_TYPE_OPTIONS: readonly SelectOption[] = [
  { value: '', label: 'All types' },
  ...WORKFLOW_TYPES.map((t) => ({ value: t, label: formatLabel(t) })),
]

export interface WorkflowFiltersProps {
  /**
   * Optional ref forwarded to the search input so the parent page
   * can focus it (e.g. after clicking "Clear filters" -- see audit
   * 47#1).
   */
  searchInputRef?: React.Ref<HTMLInputElement>
}

export function WorkflowFilters({ searchInputRef }: WorkflowFiltersProps = {}) {
  const searchQuery = useWorkflowsStore((s) => s.searchQuery)
  const workflowTypeFilter = useWorkflowsStore((s) => s.workflowTypeFilter)
  const setSearchQuery = useWorkflowsStore((s) => s.setSearchQuery)
  const setWorkflowTypeFilter = useWorkflowsStore((s) => s.setWorkflowTypeFilter)

  return (
    <div className="flex flex-wrap items-center gap-3">
      <InputField
        ref={searchInputRef}
        label="Search workflows"
        type="text"
        placeholder="Search workflows..."
        value={searchQuery}
        onValueChange={setSearchQuery}
        className="h-9 w-64"
      />

      <SelectField
        label="Workflow type"
        options={WORKFLOW_TYPE_OPTIONS}
        value={workflowTypeFilter ?? ''}
        onChange={(value) => setWorkflowTypeFilter(value || null)}
        className="h-9"
      />
    </div>
  )
}
