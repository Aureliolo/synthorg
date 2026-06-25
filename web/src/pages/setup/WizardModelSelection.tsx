import { useCallback, useEffect, useState } from 'react'
import { getModelRecommendations } from '@/api/endpoints/setup'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { Skeleton } from '@/components/ui/skeleton'
import { ToggleField } from '@/components/ui/toggle-field'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import type { SettingEntry, SettingNamespace } from '@/api/types/settings'
import type { SetupModelRecommendationsResponse } from '@/api/types/setup'

// Per-feature model pickers. Each writes straight through the settings API,
// so a choice survives independently of the wizard and stays editable in
// dashboard Settings. Research + Chief-of-Staff pick from the full catalogue
// (the decomposition candidate list); embedding has its own capable subset.
type ModelKey = 'decomposition' | 'embedding' | 'research' | 'cos'

interface PickerSpec {
  key: ModelKey
  namespace: SettingNamespace
  settingKey: string
  label: string
  hint: string
}

const PICKERS: readonly PickerSpec[] = [
  {
    key: 'decomposition',
    namespace: 'coordination',
    settingKey: 'decomposition_model',
    label: 'Coordination model',
    hint: 'Used by the coordinator to break briefs into tasks.',
  },
  {
    key: 'embedding',
    namespace: 'memory',
    settingKey: 'embedder_model',
    label: 'Embedding model',
    hint: 'Powers memory + knowledge.',
  },
  {
    key: 'research',
    namespace: 'research',
    settingKey: 'model',
    label: 'Research model',
    hint: 'The model the research pipeline reasons with.',
  },
  {
    key: 'cos',
    namespace: 'chief_of_staff',
    settingKey: 'chat_model',
    label: 'Chief of Staff model',
    hint: 'Powers the conversational Chief-of-Staff turns.',
  },
]

type ModelChoices = Record<ModelKey, string>
interface ToggleChoices {
  research: boolean
  knowledge: boolean
}

function toOptions(ids: readonly string[]): readonly SelectOption[] {
  return ids.map((id) => ({ value: id, label: id }))
}

function valueOf(entries: readonly SettingEntry[], key: string): string | undefined {
  const found = entries.find((entry) => entry.definition.key === key)
  return found && found.value ? found.value : undefined
}

function boolOf(entries: readonly SettingEntry[], key: string): boolean {
  // The settings default ships "true" for these on-by-default flags, so a
  // missing entry is treated as enabled.
  const found = entries.find((entry) => entry.definition.key === key)
  return found ? found.value === 'true' : true
}

function candidatesFor(
  recs: SetupModelRecommendationsResponse,
  key: ModelKey,
): readonly string[] {
  return key === 'embedding' ? recs.embedding_candidates : recs.decomposition_candidates
}

interface NamespaceEntries {
  coordination: readonly SettingEntry[]
  memory: readonly SettingEntry[]
  research: readonly SettingEntry[]
  chief_of_staff: readonly SettingEntry[]
  knowledge: readonly SettingEntry[]
}

// Prefer a persisted operator choice, then the backend recommendation,
// then empty. Extracted so ``buildChoices`` stays under the complexity cap.
function pickModel(
  persisted: string | undefined,
  recommended: string | null | undefined,
): string {
  return persisted ?? recommended ?? ''
}

function buildChoices(
  recs: SetupModelRecommendationsResponse,
  ns: NamespaceEntries,
): { models: ModelChoices; toggles: ToggleChoices } {
  return {
    models: {
      decomposition: pickModel(
        valueOf(ns.coordination, 'decomposition_model'),
        recs.decomposition_recommended,
      ),
      embedding: pickModel(
        valueOf(ns.memory, 'embedder_model'),
        recs.embedding_recommended,
      ),
      research: pickModel(valueOf(ns.research, 'model'), recs.research_recommended),
      cos: pickModel(valueOf(ns.chief_of_staff, 'chat_model'), recs.cos_recommended),
    },
    toggles: {
      research: boolOf(ns.research, 'enabled'),
      knowledge: boolOf(ns.knowledge, 'enabled'),
    },
  }
}

interface LoadHandlers {
  setRecs: (value: SetupModelRecommendationsResponse) => void
  setModels: (value: ModelChoices) => void
  setToggles: (value: ToggleChoices) => void
  setError: (value: string) => void
  setLoading: (value: boolean) => void
}

async function loadModelSelection(
  isCancelled: () => boolean,
  handlers: LoadHandlers,
): Promise<void> {
  try {
    const [recs, coordination, memory, research, chief_of_staff, knowledge] =
      await Promise.all([
        getModelRecommendations(),
        getNamespaceSettings('coordination'),
        getNamespaceSettings('memory'),
        getNamespaceSettings('research'),
        getNamespaceSettings('chief_of_staff'),
        getNamespaceSettings('knowledge'),
      ])
    if (isCancelled()) return
    handlers.setRecs(recs)
    const { models, toggles } = buildChoices(recs, {
      coordination,
      memory,
      research,
      chief_of_staff,
      knowledge,
    })
    handlers.setModels(models)
    handlers.setToggles(toggles)
  } catch (caught) {
    if (!isCancelled()) handlers.setError(getErrorMessage(caught))
  } finally {
    if (!isCancelled()) handlers.setLoading(false)
  }
}

const EMPTY_MODELS: ModelChoices = {
  decomposition: '',
  embedding: '',
  research: '',
  cos: '',
}

interface ModelSelectionState {
  recs: SetupModelRecommendationsResponse | null
  models: ModelChoices
  toggles: ToggleChoices
  loading: boolean
  error: string | null
  selectModel: (spec: PickerSpec, value: string) => void
  toggleFeature: (
    name: keyof ToggleChoices,
    namespace: SettingNamespace,
    value: boolean,
  ) => void
}

function useWizardModelSelection(): ModelSelectionState {
  const [recs, setRecs] = useState<SetupModelRecommendationsResponse | null>(null)
  const [models, setModels] = useState<ModelChoices>(EMPTY_MODELS)
  const [toggles, setToggles] = useState<ToggleChoices>({
    research: true,
    knowledge: true,
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const addToast = useToastStore((s) => s.add)

  useEffect(() => {
    let cancelled = false
    void loadModelSelection(() => cancelled, {
      setRecs,
      setModels,
      setToggles,
      setError,
      setLoading,
    })
    return () => {
      cancelled = true
    }
  }, [])

  const selectModel = useCallback(
    (spec: PickerSpec, value: string) => {
      setModels((prev) => ({ ...prev, [spec.key]: value }))
      void updateSetting(spec.namespace, spec.settingKey, { value }).catch(
        (caught: unknown) => {
          addToast({
            variant: 'error',
            title: `Could not save the ${spec.label.toLowerCase()}`,
            description: getErrorMessage(caught),
          })
        },
      )
    },
    [addToast],
  )

  const toggleFeature = useCallback(
    (name: keyof ToggleChoices, namespace: SettingNamespace, value: boolean) => {
      setToggles((prev) => ({ ...prev, [name]: value }))
      void updateSetting(namespace, 'enabled', {
        value: value ? 'true' : 'false',
      }).catch((caught: unknown) => {
        addToast({
          variant: 'error',
          title: `Could not save the ${name} setting`,
          description: getErrorMessage(caught),
        })
      })
    },
    [addToast],
  )

  return { recs, models, toggles, loading, error, selectModel, toggleFeature }
}

/**
 * Per-feature model pickers for the wizard Agents step.
 *
 * Surfaces the coordinator's decomposition model, the embedding model (which
 * powers memory + knowledge), the research model (with an enable toggle), and
 * the Chief-of-Staff model, plus a knowledge enable toggle. Each is prefilled
 * with a sensible recommendation and overridable from the catalogue; every
 * change writes straight through the settings API.
 */
export function WizardModelSelection() {
  const { recs, models, toggles, loading, error, selectModel, toggleFeature } =
    useWizardModelSelection()

  if (loading) {
    return <Skeleton className="h-40 w-full" />
  }
  if (error) {
    return (
      <ErrorBanner
        variant="section"
        severity="warning"
        title="Could not load model recommendations"
        description={`${error} You can set these later in Settings.`}
      />
    )
  }
  if (!recs) {
    return null
  }

  const visiblePickers = PICKERS.filter(
    (spec) => spec.key !== 'research' || toggles.research,
  )
  return (
    <section className="space-y-section-gap rounded-lg border border-border bg-card p-card">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-foreground">Models</h3>
        <p className="text-xs text-muted-foreground">
          Prefilled with our recommendations. Override any of them now or later
          in Settings.
        </p>
      </div>

      {visiblePickers.map((spec) => (
        <SelectField
          key={spec.key}
          label={spec.label}
          hint={spec.hint}
          value={models[spec.key]}
          onChange={(value) => {
            selectModel(spec, value)
          }}
          options={toOptions(candidatesFor(recs, spec.key))}
        />
      ))}

      <ToggleField
        label="Research"
        description="Let agents run research briefs. Turn off to disable research."
        checked={toggles.research}
        onChange={(checked) => {
          toggleFeature('research', 'research', checked)
        }}
      />
      <ToggleField
        label="Knowledge base"
        description="Document ingestion + retrieval over the memory backend. Uses the embedding model above."
        checked={toggles.knowledge}
        onChange={(checked) => {
          toggleFeature('knowledge', 'knowledge', checked)
        }}
      />
    </section>
  )
}
