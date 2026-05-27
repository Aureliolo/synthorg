import {
  BadgeCheck,
  CircleDashed,
  DollarSign,
  Download,
  GitBranch,
  Map as MapIcon,
  Maximize,
  Network,
  Plus,
  Printer,
  Sparkles,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { SegmentedControl, type SegmentedControlOption } from '@/components/ui/segmented-control'
import { useToolbarKeyboardNav } from '@/hooks/useToolbarKeyboardNav'
import { cn } from '@/lib/utils'
import {
  useOrgChartPrefs,
  type ParticleFlowMode,
} from '@/stores/org-chart-prefs'

export type ViewMode = 'hierarchy' | 'force'

interface OrgChartToolbarProps {
  viewMode: ViewMode
  onViewModeChange: (mode: ViewMode) => void
  onFitView: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  /** Export the current chart as a PNG file. */
  onExportPng?: () => void
  exporting?: boolean
  /** Trigger the browser's print flow with the chart in focus. */
  onPrint?: () => void
  className?: string
}

const PARTICLE_OPTIONS: readonly SegmentedControlOption<ParticleFlowMode>[] = [
  { value: 'always', label: 'Flow' },
  { value: 'live', label: 'Live' },
  { value: 'off', label: 'Off' },
]

const STATUS_DOTS_TOOLTIP =
  'Status dots: one colored dot per agent in the dept\n' +
  '  • Green = active (working on a task)\n' +
  '  • Blue = idle (ready, not assigned)\n' +
  '  • Red = error\n' +
  '  • Gray = offline'

const Divider = () => <div className="mx-1 h-5 w-px bg-border" />

/**
 * Small inline icon-toggle button used in the toolbar's "show on
 * cards" strip.  Keeps the row compact and shows the current on/off
 * state via the accent fill + icon tint.  A title attribute provides
 * the hover tooltip.
 */
function InlineToggle({
  label,
  tooltip,
  icon: Icon,
  checked,
  onToggle,
}: {
  label: string
  tooltip: string
  icon: typeof Sparkles
  checked: boolean
  onToggle: (next: boolean) => void
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={checked}
      title={`${tooltip} (${checked ? 'on' : 'off'})`}
      onClick={() => onToggle(!checked)}
      className={cn(
        'inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-xs font-medium transition-colors',
        checked
          ? 'bg-accent/15 text-accent hover:bg-accent/25'
          : 'text-text-muted hover:bg-border/40 hover:text-foreground',
      )}
    >
      <Icon className="size-3.5" aria-hidden="true" />
    </button>
  )
}

interface ViewModeToggleProps {
  viewMode: ViewMode
  onViewModeChange: (mode: ViewMode) => void
}

function ViewModeToggle({ viewMode, onViewModeChange }: ViewModeToggleProps) {
  return (
    <div className="flex items-center rounded-md border border-border">
      <Button
        variant={viewMode === 'hierarchy' ? 'default' : 'ghost'}
        size="sm"
        onClick={() => onViewModeChange('hierarchy')}
        aria-label="Hierarchy view"
        aria-pressed={viewMode === 'hierarchy'}
        className="h-7 gap-1.5 rounded-r-none px-2 text-xs"
      >
        <GitBranch className="size-3.5" aria-hidden="true" />
        Hierarchy
      </Button>
      <Button
        variant={viewMode === 'force' ? 'default' : 'ghost'}
        size="sm"
        onClick={() => onViewModeChange('force')}
        aria-label="Communication view"
        aria-pressed={viewMode === 'force'}
        className="h-7 gap-1.5 rounded-l-none px-2 text-xs"
      >
        <Network className="size-3.5" aria-hidden="true" />
        Communication
      </Button>
    </div>
  )
}

/** Inline icon toggles, one per visual element rendered on dept cards. */
function ShowOnCardsStrip() {
  const showAddAgentButton = useOrgChartPrefs((s) => s.showAddAgentButton)
  const setShowAddAgentButton = useOrgChartPrefs((s) => s.setShowAddAgentButton)
  const showLeadBadge = useOrgChartPrefs((s) => s.showLeadBadge)
  const setShowLeadBadge = useOrgChartPrefs((s) => s.setShowLeadBadge)
  const showBudgetBar = useOrgChartPrefs((s) => s.showBudgetBar)
  const setShowBudgetBar = useOrgChartPrefs((s) => s.setShowBudgetBar)
  const showStatusDots = useOrgChartPrefs((s) => s.showStatusDots)
  const setShowStatusDots = useOrgChartPrefs((s) => s.setShowStatusDots)
  const showMinimap = useOrgChartPrefs((s) => s.showMinimap)
  const setShowMinimap = useOrgChartPrefs((s) => s.setShowMinimap)
  return (
    <div className="flex items-center gap-0.5" role="group" aria-label="Show on cards">
      <InlineToggle
        label="Toggle add agent button"
        tooltip="Add agent button"
        icon={Plus}
        checked={showAddAgentButton}
        onToggle={setShowAddAgentButton}
      />
      <InlineToggle
        label="Toggle LEAD badge"
        tooltip="LEAD badge"
        icon={BadgeCheck}
        checked={showLeadBadge}
        onToggle={setShowLeadBadge}
      />
      <InlineToggle
        label="Toggle budget bar"
        tooltip="Budget bar"
        icon={DollarSign}
        checked={showBudgetBar}
        onToggle={setShowBudgetBar}
      />
      <InlineToggle
        label="Toggle status dots"
        tooltip={STATUS_DOTS_TOOLTIP}
        icon={CircleDashed}
        checked={showStatusDots}
        onToggle={setShowStatusDots}
      />
      <InlineToggle
        label="Toggle minimap"
        tooltip="Minimap"
        icon={MapIcon}
        checked={showMinimap}
        onToggle={setShowMinimap}
      />
    </div>
  )
}

interface ZoomControlsProps {
  onFitView: () => void
  onZoomIn: () => void
  onZoomOut: () => void
}

function ZoomControls({ onFitView, onZoomIn, onZoomOut }: ZoomControlsProps) {
  return (
    <>
      <Button variant="ghost" size="sm" onClick={onFitView} aria-label="Fit to view" className="size-7 p-0">
        <Maximize className="size-3.5" aria-hidden="true" />
      </Button>
      <Button variant="ghost" size="sm" onClick={onZoomIn} aria-label="Zoom in" className="size-7 p-0">
        <ZoomIn className="size-3.5" aria-hidden="true" />
      </Button>
      <Button variant="ghost" size="sm" onClick={onZoomOut} aria-label="Zoom out" className="size-7 p-0">
        <ZoomOut className="size-3.5" aria-hidden="true" />
      </Button>
    </>
  )
}

interface ExportControlsProps {
  onExportPng?: () => void
  exporting: boolean
  onPrint?: () => void
}

function ExportControls({ onExportPng, exporting, onPrint }: ExportControlsProps) {
  if (!onExportPng && !onPrint) return null
  return (
    <>
      <Divider />
      {onExportPng && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onExportPng}
          aria-label={exporting ? 'Exporting PNG' : 'Export as PNG'}
          title={exporting ? 'Exporting PNG' : 'Export as PNG'}
          className="size-7 p-0"
          disabled={exporting}
        >
          <Download className="size-3.5" aria-hidden="true" />
        </Button>
      )}
      {onPrint && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onPrint}
          aria-label="Print org chart"
          title="Print org chart"
          className="size-7 p-0"
        >
          <Printer className="size-3.5" aria-hidden="true" />
        </Button>
      )}
    </>
  )
}

export function OrgChartToolbar({
  viewMode,
  onViewModeChange,
  onFitView,
  onZoomIn,
  onZoomOut,
  onExportPng,
  exporting = false,
  onPrint,
  className,
}: OrgChartToolbarProps) {
  const particleFlowMode = useOrgChartPrefs((s) => s.particleFlowMode)
  const setParticleFlowMode = useOrgChartPrefs((s) => s.setParticleFlowMode)
  const { ref: toolbarRef, onKeyDown } = useToolbarKeyboardNav<HTMLDivElement>()

  return (
    <div
      ref={toolbarRef}
      onKeyDown={onKeyDown}
      role="toolbar"
      aria-label="Org chart controls"
      aria-orientation="horizontal"
      className={cn(
        'flex flex-wrap items-center gap-1 rounded-lg border border-border bg-card p-1',
        className,
      )}
      data-testid="org-chart-toolbar"
    >
      <ViewModeToggle viewMode={viewMode} onViewModeChange={onViewModeChange} />
      <Divider />
      <SegmentedControl
        label="Particle flow"
        options={PARTICLE_OPTIONS}
        value={particleFlowMode}
        onChange={setParticleFlowMode}
        size="sm"
      />
      <Divider />
      <ShowOnCardsStrip />
      <Divider />
      <ZoomControls onFitView={onFitView} onZoomIn={onZoomIn} onZoomOut={onZoomOut} />
      <ExportControls onExportPng={onExportPng} exporting={exporting} onPrint={onPrint} />
    </div>
  )
}
