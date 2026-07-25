import { useCallback, useEffect, useRef, useState } from 'react'
import { getModelRecommendations } from '@/api/endpoints/setup'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { Skeleton } from '@/components/ui/skeleton'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { normalizeModelRef } from '@/utils/model-ref'
import type { SettingEntry } from '@/api/types/settings'
import type {
  SetupModelCandidate,
  SetupModelRecommendationsResponse,
} from '@/api/types/setup'

// Per-feature model pickers. Each writes straight through the settings API,
// so a choice survives independently of the wizard and stays editable in
// dashboard Settings. All non-embedding pickers select from the full
// catalogue (the decomposition candidate list); embedding has its own subset.
type ModelKey =
  | 'decomposition'
  | 'embedding'
  | 'research'
  | 'cos'
  | 'propose'
  | 'routing'
  | 'narrative'
  | 'charter'

// The subset of namespaces these pickers write to. Narrower than
// ``SettingNamespace`` so ``NamespaceEntries`` can be indexed by it directly.
type PickerNamespace =
  | 'coordination'
  | 'memory'
  | 'research'
  | 'chief_of_staff'
  | 'charter'

// How a picker's chosen value is written back. Every per-feature model setting
// is a backend ``MODEL_REF``, which rejects a provider-less value at write
// time, so those pickers select a serialized ``{provider, model_id}`` ref.
// ``memory.embedder_model`` is a plain string setting and takes a bare id.
type ValueKind = 'model_ref' | 'plain'

interface PickerSpec {
  key: ModelKey
  namespace: PickerNamespace
  settingKey: string
  label: string
  hint: string
  valueKind: ValueKind
}

const PICKERS: readonly PickerSpec[] = [
  {
    key: 'decomposition',
    namespace: 'coordination',
    settingKey: 'decomposition_model',
    label: 'Coordination model',
    hint: 'Used by the coordinator to break briefs into tasks.',
    valueKind: 'model_ref',
  },
  {
    key: 'embedding',
    namespace: 'memory',
    settingKey: 'embedder_model',
    label: 'Embedding model',
    hint: 'Powers memory + knowledge.',
    valueKind: 'plain',
  },
  {
    key: 'research',
    namespace: 'research',
    settingKey: 'model',
    label: 'Research model',
    hint: 'The model the research pipeline reasons with.',
    valueKind: 'model_ref',
  },
  {
    key: 'cos',
    namespace: 'chief_of_staff',
    settingKey: 'chat_model',
    label: 'Chief of Staff model',
    hint: 'Powers the conversational Chief-of-Staff turns.',
    valueKind: 'model_ref',
  },
  {
    key: 'propose',
    namespace: 'chief_of_staff',
    settingKey: 'propose_model',
    label: 'Request-work model',
    hint: 'Turns natural-language requests into concrete work proposals.',
    valueKind: 'model_ref',
  },
  {
    key: 'routing',
    namespace: 'chief_of_staff',
    settingKey: 'routing_model',
    label: 'Concern-routing model',
    hint: 'Classifies which role should handle an incoming concern.',
    valueKind: 'model_ref',
  },
  {
    key: 'narrative',
    namespace: 'chief_of_staff',
    settingKey: 'narrative_model',
    label: 'Run-narrative model',
    hint: 'Writes the documentary-style narrative of a run.',
    valueKind: 'model_ref',
  },
  {
    key: 'charter',
    namespace: 'charter',
    settingKey: 'interview_model',
    label: 'Project-charter model',
    hint: "Interviews you and drafts a new project's charter.",
    valueKind: 'model_ref',
  },
]

type ModelChoices = Record<ModelKey, string>

function emptyChoices(): ModelChoices {
  return PICKERS.reduce<ModelChoices>(
    (acc, spec) => ({ ...acc, [spec.key]: '' }),
    {} as ModelChoices,
  )
}

// A partial/garbled recommendations payload can leave a candidate list absent
// at runtime even though the type marks it required, so both builders below
// take an optional list and degrade to an empty picker rather than crashing
// the whole step.
function plainOptions(ids: readonly string[] | undefined): readonly SelectOption[] {
  return (ids ?? []).map((id) => ({ value: id, label: id }))
}

// The option value is the serialized ref the settings write needs; the label
// names the provider too, so the same model id served by two providers stays
// distinguishable in the list.
function refOptions(
  candidates: readonly SetupModelCandidate[] | undefined,
): readonly SelectOption[] {
  return (candidates ?? []).map((candidate) => ({
    value: normalizeModelRef(candidate.ref),
    label: `${candidate.model_id} (${candidate.provider})`,
  }))
}

function optionsFor(
  recs: SetupModelRecommendationsResponse,
  spec: PickerSpec,
): readonly SelectOption[] {
  return spec.valueKind === 'plain'
    ? plainOptions(recs.embedding_candidates)
    : refOptions(recs.decomposition_candidates)
}

function valueOf(entries: readonly SettingEntry[], key: string): string | undefined {
  const found = entries.find((entry) => entry.definition.key === key)
  // A whitespace-only stored value is not a real choice; treat it as unset so
  // the recommendation still wins and the select never renders a blank option.
  const value = found?.value.trim()
  return value ? value : undefined
}

function recommendedFor(
  recs: SetupModelRecommendationsResponse,
  key: ModelKey,
): string | null {
  const byKey: Record<ModelKey, string | null> = {
    decomposition: recs.decomposition_recommended,
    embedding: recs.embedding_recommended,
    research: recs.research_recommended,
    cos: recs.cos_recommended,
    propose: recs.propose_recommended,
    routing: recs.routing_recommended,
    narrative: recs.narrative_recommended,
    charter: recs.charter_recommended,
  }
  return byKey[key]
}

interface NamespaceEntries {
  coordination: readonly SettingEntry[]
  memory: readonly SettingEntry[]
  research: readonly SettingEntry[]
  chief_of_staff: readonly SettingEntry[]
  charter: readonly SettingEntry[]
}

// A MODEL_REF value reaches the wizard in whichever JSON spelling last wrote
// it (the backend pads after ``:``, the dashboard does not), so canonicalise
// before comparing: the select preselects by string identity against its
// option values.
function canonical(spec: PickerSpec, value: string): string {
  return spec.valueKind === 'model_ref' ? normalizeModelRef(value) : value
}

// Prefer a persisted operator choice, then the backend recommendation,
// then empty.
function pickModel(
  spec: PickerSpec,
  persisted: string | undefined,
  recommended: string | null | undefined,
): string {
  const chosen = persisted ?? recommended ?? ''
  return chosen ? canonical(spec, chosen) : ''
}

function buildChoices(
  recs: SetupModelRecommendationsResponse,
  ns: NamespaceEntries,
): ModelChoices {
  return PICKERS.reduce<ModelChoices>(
    (acc, spec) => ({
      ...acc,
      [spec.key]: pickModel(
        spec,
        valueOf(ns[spec.namespace], spec.settingKey),
        recommendedFor(recs, spec.key),
      ),
    }),
    {} as ModelChoices,
  )
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
    const [recs, coordination, memory, research, chief_of_staff, charter] =
      await Promise.all([
        getModelRecommendations(),
        getNamespaceSettings('coordination'),
        getNamespaceSettings('memory'),
        getNamespaceSettings('research'),
        getNamespaceSettings('chief_of_staff'),
        getNamespaceSettings('charter'),
      ])
    if (isCancelled()) return
    handlers.setRecs(recs)
    handlers.setModels(
      buildChoices(recs, {
        coordination,
        memory,
        research,
        chief_of_staff,
        charter,
      }),
    )
  } catch (caught) {
    if (!isCancelled()) handlers.setError(getErrorMessage(caught))
  } finally {
    if (!isCancelled()) handlers.setLoading(false)
  }
}

const EMPTY_MODELS: ModelChoices = emptyChoices()

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
  return useRef<Record<ModelKey, number>>(
    PICKERS.reduce<Record<ModelKey, number>>(
      (acc, spec) => ({ ...acc, [spec.key]: 0 }),
      {} as Record<ModelKey, number>,
    ),
  )
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
 * Surfaces every per-feature model whose live default is blank: the
 * coordinator's decomposition model, the embedding model (memory + knowledge),
 * the research model, and each Chief-of-Staff and charter model. Each is
 * prefilled with a tier-appropriate recommendation and overridable from the
 * catalogue; every change writes straight through the settings API. The
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
            options={optionsFor(recs, spec)}
          />
        ))}
      </div>
    </SectionCard>
  )
}
