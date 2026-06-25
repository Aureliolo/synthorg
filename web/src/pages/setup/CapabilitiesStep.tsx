import { useCallback, useEffect, useState } from 'react'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import { Collapsible } from '@/components/ui/collapsible'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { ToggleField } from '@/components/ui/toggle-field'
import { RestartBadge } from '@/pages/settings/RestartBadge'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { useClearStepRevalidationOnMount, useStepCompletionSync } from './_hooks'
import type { SettingEntry, SettingNamespace } from '@/api/types/settings'

// One capability toggle: a single boolean setting plus the copy that
// explains the trade-off. ``restart`` rows persist immediately but apply
// on the next restart (the service is built at boot).
interface CapabilityRow {
  namespace: SettingNamespace
  key: string
  label: string
  caption: string
  restart?: boolean
}

interface CapabilityGroup {
  title: string
  caption: string
  // Advanced groups (off by default) render collapsed; the two on-by-default
  // groups render expanded so the safe defaults are front-and-centre.
  advanced: boolean
  rows: readonly CapabilityRow[]
}

const GROUPS: readonly CapabilityGroup[] = [
  {
    title: 'Conversational',
    caption: 'Talk to your org through the Chief of Staff.',
    advanced: false,
    rows: [
      {
        namespace: 'chief_of_staff',
        key: 'explain_chat_enabled',
        label: 'Explain chat',
        caption: 'Ask the Chief of Staff to explain proposals, alerts, and signals.',
      },
      {
        namespace: 'chief_of_staff',
        key: 'propose_enabled',
        label: 'Proposals',
        caption: 'Clarify a request and park proposed work for your approval.',
      },
      {
        namespace: 'chief_of_staff',
        key: 'routing_enabled',
        label: 'Concern routing',
        caption: 'Route each turn to the most-senior relevant role agent.',
        restart: true,
      },
      {
        namespace: 'chief_of_staff',
        key: 'group_chat_enabled',
        label: 'Group chat',
        caption: 'Hold a multi-agent conversation with several role agents.',
      },
    ],
  },
  {
    title: 'Knowledge & research',
    caption: 'Ground work in documents and let agents research.',
    advanced: false,
    rows: [
      {
        namespace: 'research',
        key: 'enabled',
        label: 'Research',
        caption: 'Let agents run research briefs.',
        restart: true,
      },
      {
        namespace: 'knowledge',
        key: 'enabled',
        label: 'Knowledge base',
        caption: 'Document ingestion + retrieval over the memory backend.',
        restart: true,
      },
    ],
  },
  {
    title: 'Automation',
    caption: 'Advanced. Off by default: these run, spend, or change things on their own.',
    advanced: true,
    rows: [
      {
        namespace: 'self_improvement',
        key: 'enabled',
        label: 'Self-improvement',
        caption: 'The system proposes changes to itself; a bad proposal can break a running org.',
        restart: true,
      },
      {
        namespace: 'self_improvement',
        key: 'tool_creation_enabled',
        label: 'Toolsmith',
        caption: 'Builds new tools on its own (requires a capability allowlist).',
        restart: true,
      },
      {
        namespace: 'engine',
        key: 'evolution_enabled',
        label: 'Agent evolution',
        caption: 'Mutates agent identities every turn.',
        restart: true,
      },
      {
        namespace: 'providers',
        key: 'model_refresh_auto_apply_within_family',
        label: 'Model auto-apply',
        caption: 'Silently reassigns agents to newer models in the same family.',
      },
      {
        namespace: 'chief_of_staff',
        key: 'learning_enabled',
        label: 'Learning',
        caption: 'Tunes proposal confidence from past approvals in the background.',
        restart: true,
      },
    ],
  },
  {
    title: 'External network egress',
    caption: 'Advanced. Off by default for security: this lets agents reach external hosts.',
    advanced: true,
    rows: [
      {
        namespace: 'external_api',
        key: 'enabled',
        label: 'External API access',
        caption: 'Lets agents call arbitrary external APIs (an SSRF / exfiltration surface).',
        restart: true,
      },
    ],
  },
  {
    title: 'Acts on your behalf',
    caption: 'Off by default: these let the org act without you in the loop.',
    advanced: true,
    rows: [
      {
        namespace: 'chief_of_staff',
        key: 'direct_mcp_enabled',
        label: 'Direct MCP acting',
        caption: 'A chat instruction drives a real action under the agent trust level.',
        restart: true,
      },
      {
        namespace: 'chief_of_staff',
        key: 'invite_enabled',
        label: 'Agent invite',
        caption: 'Agents pull other agents into a chat on their own (gated by your consent).',
        restart: true,
      },
    ],
  },
]

const DISTINCT_NAMESPACES: readonly SettingNamespace[] = [
  'chief_of_staff',
  'research',
  'knowledge',
  'self_improvement',
  'engine',
  'providers',
  'external_api',
]

function rowId(row: CapabilityRow): string {
  return `${row.namespace}/${row.key}`
}

// Resolve a flag from the namespace entries. The backend ships every
// registered key (resolved to its default), so ``fallback`` only matters
// for the degenerate empty-namespace case; it mirrors the row's posture
// default (on for the two safe groups, off for the advanced ones) so this
// surface never disagrees with the Models section's research/knowledge
// toggles on a fresh install.
function boolOf(
  entries: readonly SettingEntry[],
  key: string,
  fallback: boolean,
): boolean {
  const found = entries.find((entry) => entry.definition.key === key)
  return found ? found.value === 'true' : fallback
}

interface LoadHandlers {
  setValues: (value: Record<string, boolean>) => void
  setError: (value: string) => void
  setLoading: (value: boolean) => void
}

async function loadCapabilities(
  isCancelled: () => boolean,
  handlers: LoadHandlers,
): Promise<void> {
  try {
    const results = await Promise.all(
      DISTINCT_NAMESPACES.map((ns) => getNamespaceSettings(ns)),
    )
    if (isCancelled()) return
    const byNs = new Map<SettingNamespace, readonly SettingEntry[]>()
    DISTINCT_NAMESPACES.forEach((ns, index) => byNs.set(ns, results[index] ?? []))
    const values: Record<string, boolean> = {}
    for (const group of GROUPS) {
      for (const row of group.rows) {
        values[rowId(row)] = boolOf(
          byNs.get(row.namespace) ?? [],
          row.key,
          !group.advanced,
        )
      }
    }
    handlers.setValues(values)
  } catch (caught) {
    if (!isCancelled()) handlers.setError(getErrorMessage(caught))
  } finally {
    if (!isCancelled()) handlers.setLoading(false)
  }
}

interface CapabilitiesState {
  values: Record<string, boolean>
  loading: boolean
  error: string | null
  toggle: (row: CapabilityRow, value: boolean) => void
}

function useCapabilities(): CapabilitiesState {
  const [values, setValues] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const addToast = useToastStore((s) => s.add)

  useEffect(() => {
    let cancelled = false
    void loadCapabilities(() => cancelled, { setValues, setError, setLoading })
    return () => {
      cancelled = true
    }
  }, [])

  const toggle = useCallback(
    (row: CapabilityRow, value: boolean) => {
      // Optimistic write; restore the prior value if the API call fails so
      // the switch never lies about the persisted state.
      let previous = false
      setValues((prev) => {
        previous = prev[rowId(row)] ?? false
        return { ...prev, [rowId(row)]: value }
      })
      void updateSetting(row.namespace, row.key, {
        value: value ? 'true' : 'false',
      }).catch((caught: unknown) => {
        setValues((prev) => ({ ...prev, [rowId(row)]: previous }))
        addToast({
          variant: 'error',
          title: `Could not save the ${row.label.toLowerCase()} setting`,
          description: getErrorMessage(caught),
        })
      })
    },
    [addToast],
  )

  return { values, loading, error, toggle }
}

function CapabilityRowView({
  row,
  checked,
  onToggle,
}: {
  row: CapabilityRow
  checked: boolean
  onToggle: (row: CapabilityRow, value: boolean) => void
}) {
  return (
    <div className="flex items-start justify-between gap-grid-gap">
      <ToggleField
        label={row.label}
        description={row.caption}
        checked={checked}
        onChange={(value) => {
          onToggle(row, value)
        }}
      />
      {row.restart === true && <RestartBadge className="mt-1 shrink-0" />}
    </div>
  )
}

function CapabilityRows({
  group,
  values,
  onToggle,
}: {
  group: CapabilityGroup
  values: Record<string, boolean>
  onToggle: (row: CapabilityRow, value: boolean) => void
}) {
  return (
    <div className="space-y-section-gap">
      {group.rows.map((row) => (
        <CapabilityRowView
          key={rowId(row)}
          row={row}
          checked={values[rowId(row)] ?? false}
          onToggle={onToggle}
        />
      ))}
    </div>
  )
}

function CapabilityGroupView({
  group,
  values,
  onToggle,
}: {
  group: CapabilityGroup
  values: Record<string, boolean>
  onToggle: (row: CapabilityRow, value: boolean) => void
}) {
  const rows = <CapabilityRows group={group} values={values} onToggle={onToggle} />
  if (group.advanced) {
    return (
      <Collapsible
        title={group.title}
        summary={<span className="text-xs text-muted-foreground">{group.caption}</span>}
        defaultOpen={false}
      >
        {rows}
      </Collapsible>
    )
  }
  return (
    <SectionCard title={group.title}>
      <p className="mb-section-gap text-xs text-muted-foreground">{group.caption}</p>
      {rows}
    </SectionCard>
  )
}

/**
 * Wizard Capabilities step.
 *
 * Surfaces the impactful feature toggles, grouped so the trade-off is
 * obvious and pre-set to the on-by-default posture: the two safe groups
 * are expanded and on, the three advanced groups (autonomous spend,
 * network egress, acts-on-your-behalf) are collapsed and off. Clicking
 * Next yields the sane defaults; every toggle is also in dashboard
 * Settings afterwards. Each change writes straight through the settings
 * API; rows marked with a restart badge apply on the next restart.
 */
export function CapabilitiesStep() {
  const { values, loading, error, toggle } = useCapabilities()
  useStepCompletionSync('capabilities', true)
  useClearStepRevalidationOnMount('capabilities')

  if (loading) {
    return <Skeleton className="h-96 w-full" />
  }
  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Capabilities</h2>
        <p className="text-sm text-muted-foreground">
          Defaults are tuned for a safe, useful org. Everything here is also in
          Settings later, so you can change your mind any time.
        </p>
      </div>

      {error !== null ? (
        <ErrorBanner
          variant="section"
          severity="warning"
          title="Could not load capabilities"
          description={`${error} You can set these later in Settings.`}
        />
      ) : (
        GROUPS.map((group) => (
          <CapabilityGroupView
            key={group.title}
            group={group}
            values={values}
            onToggle={toggle}
          />
        ))
      )}
    </div>
  )
}
