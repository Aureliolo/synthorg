import { useCallback, useEffect, useRef, useState } from 'react'
import { getModelRecommendations } from '@/api/endpoints/setup'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { Skeleton } from '@/components/ui/skeleton'
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

function toOptions(ids: readonly string[] | undefined): readonly SelectOption[] {
  // A partial/garbled recommendations payload can leave a candidate list
  // absent at runtime even though the type marks it required; degrade to an
  // empty picker rather than crashing the whole step.
  return (ids ?? []).map((id) => ({ value: id, label: id }))
}

function valueOf(entries: readonly SettingEntry[], key: string): string | undefined {
  const found = entries.find((entry) => entry.definition.key === key)
  // A whitespace-only stored value is not a real choice; treat it as unset so
  // the recommendation still wins and the select never renders a blank option.
  const value = found?.value.trim()
  return value ? value : undefined
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
): ModelChoices {
  return {
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
  }
}

interface LoadHandlers {
  setRecs: (value: SetupModelRecommendationsResponse) => void
  setModels: (value: ModelChoices) => void
  setError: (value: string) => void
  setLoading: (value: boolean) => void
}

async function loadModelSelection(
  isCancelled: () => boolean,
  handlers: LoadHandlers,
): Promise<void> {
  try {
    const [recs, coordination, memory, research, chief_of_staff] =
      await Promise.all([
        getModelRecommendations(),
        getNamespaceSettings('coordination'),
        getNamespaceSettings('memory'),
        getNamespaceSettings('research'),
        getNamespaceSettings('chief_of_staff'),
      ])
    if (isCancelled()) return
    handlers.setRecs(recs)
    handlers.setModels(
      buildChoices(recs, { coordination, memory, research, chief_of_staff }),
    )
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
  loading: boolean
  error: string | null
  selectModel: (spec: PickerSpec, value: string) => void
}

// Per-key request counters: only the latest in-flight write for a given key is
// allowed to roll back on failure, so a slow earlier request that fails after a
// faster later one succeeded cannot clobber the newer value.
function useRequestIdRefs() {
  return useRef<Record<ModelKey, number>>({
    decomposition: 0,
    embedding: 0,
    research: 0,
    cos: 0,
  })
}

function useWizardModelSelection(): ModelSelectionState {
  const [recs, setRecs] = useState<SetupModelRecommendationsResponse | null>(null)
  const [models, setModels] = useState<ModelChoices>(EMPTY_MODELS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const addToast = useToastStore((s) => s.add)
  const modelRequestIdsRef = useRequestIdRefs()

  useEffect(() => {
    let cancelled = false
    void loadModelSelection(() => cancelled, {
      setRecs,
      setModels,
      setError,
      setLoading,
    })
    return () => {
      cancelled = true
    }
  }, [])

  const selectModel = useCallback(
    (spec: PickerSpec, value: string) => {
      // Optimistic write; restore the prior model id if the API call fails.
      let previous = ''
      const requestId = modelRequestIdsRef.current[spec.key] + 1
      modelRequestIdsRef.current[spec.key] = requestId
      setModels((prev) => {
        previous = prev[spec.key]
        return { ...prev, [spec.key]: value }
      })
      void updateSetting(spec.namespace, spec.settingKey, { value }).catch(
        (caught: unknown) => {
          // A newer write for this key has superseded this one: leave the value
          // and any error to that newer write, so neither a stale rollback nor a
          // stale "could not save" toast can clobber the current state.
          if (modelRequestIdsRef.current[spec.key] !== requestId) return
          setModels((prev) => ({ ...prev, [spec.key]: previous }))
          addToast({
            variant: 'error',
            title: `Could not save the ${spec.label.toLowerCase()}`,
            description: getErrorMessage(caught),
          })
        },
      )
    },
    [addToast, modelRequestIdsRef],
  )

  return { recs, models, loading, error, selectModel }
}

export interface WizardModelSelectionProps {
  /** Hide the Research model picker when research is disabled. The toggle that
   *  drives this lives in the Capabilities step's Knowledge & research group. */
  researchEnabled: boolean
}

/**
 * Per-feature model-default pickers for the wizard Capabilities step.
 *
 * Surfaces the coordinator's decomposition model, the embedding model (which
 * powers memory + knowledge), the research model, and the Chief-of-Staff
 * model. Each is prefilled with a sensible recommendation and overridable from
 * the catalogue; every change writes straight through the settings API. The
 * research picker is shown only while research is enabled.
 */
export function WizardModelSelection({ researchEnabled }: WizardModelSelectionProps) {
  const { recs, models, loading, error, selectModel } = useWizardModelSelection()

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
    (spec) => spec.key !== 'research' || researchEnabled,
  )
  return (
    <SectionCard title="Models">
      <div className="space-y-section-gap">
        <p className="text-xs text-muted-foreground">
          Prefilled with our recommendations. Override any of them now or later
          in Settings.
        </p>

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
      </div>
    </SectionCard>
  )
}
