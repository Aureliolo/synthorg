import { useArtifactsStore } from '@/stores/artifacts'
import { ARTIFACT_TYPE_VALUES, type ArtifactType } from '@/api/types/enums'
import { formatLabel } from '@/utils/format'

const CONTENT_TYPE_OPTIONS = [
  { value: 'text/', label: 'Text' },
  { value: 'image/', label: 'Image' },
  { value: 'application/json', label: 'JSON' },
  { value: 'application/pdf', label: 'PDF' },
  { value: 'application/', label: 'Application' },
] as const

const TEXT_INPUT_CLASSES =
  'h-9 rounded-md border border-border bg-surface px-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-accent'
const SELECT_CLASSES =
  'h-9 rounded-md border border-border bg-surface px-2 text-sm text-foreground'

export function ArtifactFilters() {
  const searchQuery = useArtifactsStore((s) => s.searchQuery)
  const typeFilter = useArtifactsStore((s) => s.typeFilter)
  const createdByFilter = useArtifactsStore((s) => s.createdByFilter)
  const taskIdFilter = useArtifactsStore((s) => s.taskIdFilter)
  const contentTypeFilter = useArtifactsStore((s) => s.contentTypeFilter)
  const projectIdFilter = useArtifactsStore((s) => s.projectIdFilter)
  const setSearchQuery = useArtifactsStore((s) => s.setSearchQuery)
  const setTypeFilter = useArtifactsStore((s) => s.setTypeFilter)
  const setCreatedByFilter = useArtifactsStore((s) => s.setCreatedByFilter)
  const setTaskIdFilter = useArtifactsStore((s) => s.setTaskIdFilter)
  const setContentTypeFilter = useArtifactsStore((s) => s.setContentTypeFilter)
  const setProjectIdFilter = useArtifactsStore((s) => s.setProjectIdFilter)

  return (
    <div className="flex flex-wrap items-center gap-3">
      <FilterTextInput
        value={searchQuery}
        onValueChange={setSearchQuery}
        placeholder="Search artifacts..."
        ariaLabel="Search artifacts"
        widthClass="w-64"
      />
      <ArtifactTypeFilter value={typeFilter} onValueChange={setTypeFilter} />
      <FilterTextInput
        value={createdByFilter ?? ''}
        onValueChange={(v) => setCreatedByFilter(v || null)}
        placeholder="Filter by agent..."
        ariaLabel="Filter by creator agent"
      />
      <FilterTextInput
        value={taskIdFilter ?? ''}
        onValueChange={(v) => setTaskIdFilter(v || null)}
        placeholder="Filter by task..."
        ariaLabel="Filter by task ID"
      />
      <ContentTypeFilter value={contentTypeFilter} onValueChange={setContentTypeFilter} />
      <FilterTextInput
        value={projectIdFilter ?? ''}
        onValueChange={(v) => setProjectIdFilter(v || null)}
        placeholder="Filter by project..."
        ariaLabel="Filter by project ID"
      />
    </div>
  )
}

interface FilterTextInputProps {
  value: string
  placeholder: string
  ariaLabel: string
  onValueChange: (value: string) => void
  widthClass?: string
}

function FilterTextInput({
  value,
  placeholder,
  ariaLabel,
  onValueChange,
  widthClass = 'w-40',
}: FilterTextInputProps) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onValueChange(e.target.value)}
      className={`${TEXT_INPUT_CLASSES} ${widthClass}`}
      aria-label={ariaLabel}
    />
  )
}

interface ArtifactTypeFilterProps {
  value: ArtifactType | null
  onValueChange: (value: ArtifactType | null) => void
}

function ArtifactTypeFilter({ value, onValueChange }: ArtifactTypeFilterProps) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => {
        const next = e.target.value
        if (!next) {
          onValueChange(null)
          return
        }
        if (ARTIFACT_TYPE_VALUES.includes(next as ArtifactType)) {
          onValueChange(next as ArtifactType)
        }
      }}
      className={SELECT_CLASSES}
      aria-label="Filter by type"
    >
      <option value="">All types</option>
      {ARTIFACT_TYPE_VALUES.map((t) => (
        <option key={t} value={t}>
          {formatLabel(t)}
        </option>
      ))}
    </select>
  )
}

interface ContentTypeFilterProps {
  value: string | null
  onValueChange: (value: string | null) => void
}

function ContentTypeFilter({ value, onValueChange }: ContentTypeFilterProps) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onValueChange(e.target.value || null)}
      className={SELECT_CLASSES}
      aria-label="Filter by content type"
    >
      <option value="">All content types</option>
      {CONTENT_TYPE_OPTIONS.map((ct) => (
        <option key={ct.value} value={ct.value}>
          {ct.label}
        </option>
      ))}
    </select>
  )
}
