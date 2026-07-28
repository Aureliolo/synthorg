import { useCallback, useEffect, useRef, useState } from 'react'
import { getModelRecommendations } from '@/api/endpoints/setup'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { Skeleton } from '@/components/ui/skeleton'
import { useToastStore } from '@/stores/toast'
import { createCancellationToken, type CancellationToken } from '@/utils/cancellation'
import { getErrorMessage } from '@/utils/errors'
import { normalizeModelRef } from '@/utils/model-ref'
import type { SettingEntry } from '@/api/types/settings'
import type {
  SetupModelCandidate,
  SetupModelRecommendationsResponse,
} from '@/api/types/setup'

// Per-feature model pickers, in render order. Each writes straight through the
// settings API, so a choice survives independently of the wizard and stays
// editable in dashboard Settings. Every picker selects a provider-bound ref;
// embedding draws from its own list, which is the same catalogue plus the
// built-in embedder.
//
// This tuple is the single source of the key union, so a picker added here
// without its spec, its recommendation mapping, or a slot in the per-key
// records below is a type error rather than a silently blank select.
const MODEL_KEYS = [
  'decomposition',
  'embedding',
  'research',
  'cos',
  'propose',
  'routing',
  'narrative',
  'charter',
] as const

type ModelKey = (typeof MODEL_KEYS)[number]

// The subset of namespaces these pickers write to. Narrower than
// ``SettingNamespace`` so ``NamespaceEntries`` can be indexed by it directly.
type PickerNamespace =
  | 'coordination'
  | 'memory'
  | 'research'
  | 'chief_of_staff'
  | 'charter'

interface PickerSpec {
  key: ModelKey
  namespace: PickerNamespace
  settingKey: string
  label: string
  hint: string
}

const PICKER_META: Record<ModelKey, Omit<PickerSpec, 'key'>> = {
  decomposition: {
    namespace: 'coordination',
    settingKey: 'decomposition_model',
    label: 'Coordination model',
    hint: 'Used by the coordinator to break briefs into tasks.',
  },
  embedding: {
    namespace: 'memory',
    settingKey: 'embedder_model',
    label: 'Embedding model',
    hint: 'How agents recall. Nothing is chosen for you; leave it unset and memory stays off.',
  },
  research: {
    namespace: 'research',
    settingKey: 'model',
    label: 'Research model',
    hint: 'The model the research pipeline reasons with.',
  },
  cos: {
    namespace: 'chief_of_staff',
    settingKey: 'chat_model',
    label: 'Chief of Staff model',
    hint: 'Powers the conversational Chief-of-Staff turns.',
  },
  propose: {
    namespace: 'chief_of_staff',
    settingKey: 'propose_model',
    label: 'Request-work model',
    hint: 'Turns natural-language requests into concrete work proposals.',
  },
  routing: {
    namespace: 'chief_of_staff',
    settingKey: 'routing_model',
    label: 'Concern-routing model',
    hint: 'Classifies which role should handle an incoming concern.',
  },
  narrative: {
    namespace: 'chief_of_staff',
    settingKey: 'narrative_model',
    label: 'Run-narrative model',
    hint: 'Writes the documentary-style narrative of a run.',
  },
  charter: {
    namespace: 'charter',
    settingKey: 'interview_model',
    label: 'Project-charter model',
    hint: "Interviews you and drafts a new project's charter.",
  },
}

const PICKERS: readonly PickerSpec[] = MODEL_KEYS.map((key) => ({
  key,
  ...PICKER_META[key],
}))

type ModelChoices = Record<ModelKey, string>

// Seeded from the key tuple, so the record is total by construction.
function perKey<V>(value: V): Record<ModelKey, V> {
  return Object.fromEntries(MODEL_KEYS.map((key) => [key, value])) as Record<
    ModelKey,
    V
  >
}

// A partial/garbled recommendations payload can leave a candidate list absent
// at runtime even though the type marks it required, so this takes an optional
// list and degrades to an empty picker rather than crashing the whole step.
//
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
  return refOptions(
    spec.key === 'embedding' ? recs.embedding_candidates : recs.model_ref_candidates,
  )
}

// Embedding has no recommendation, so an unset picker would otherwise render
// its first option and read as a choice nobody made. A disabled placeholder
// keeps "nothing selected" visibly distinct from "the built-in is selected".
function placeholderFor(spec: PickerSpec): string | undefined {
  return spec.key === 'embedding' ? 'Select an embedding model' : undefined
}

const BUILTIN_EMBEDDER_PROVIDER = 'builtin'

const BUILTIN_EMBEDDER_HINT =
  'Running without an embedding model: recall matches shared vocabulary, not meaning, so agents get literal term overlap instead of related memories. Pick a model here to recall by meaning.'

// The warning for the built-in embedder lives on this control rather than in a
// banner elsewhere: it is a consequence of this choice, so it belongs where the
// choice is made and disappears when the choice changes.
function hintFor(spec: PickerSpec, value: string): string {
  if (spec.key !== 'embedding' || !value) return spec.hint
  return providerOf(value) === BUILTIN_EMBEDDER_PROVIDER
    ? BUILTIN_EMBEDDER_HINT
    : spec.hint
}

/**
 * Read the provider out of a serialized model ref.
 *
 * @returns The provider name, or an empty string when the value is not a
 * parseable ref.
 */
function providerOf(value: string): string {
  try {
    const parsed: unknown = JSON.parse(value)
    if (parsed !== null && typeof parsed === 'object' && 'provider' in parsed) {
      const { provider } = parsed
      return typeof provider === 'string' ? provider : ''
    }
  } catch {
    return ''
  }
  return ''
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
    // Embedding is never recommended: the operator names it, or memory
    // stays off. A prefilled default here is how a model whose width the
    // vector store cannot index got chosen without anyone deciding to.
    embedding: null,
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
function canonical(value: string): string {
  return normalizeModelRef(value)
}

// Prefer a persisted operator choice, then the backend recommendation,
// then empty.
function pickModel(
  persisted: string | undefined,
  recommended: string | null | undefined,
): string {
  const chosen = persisted ?? recommended ?? ''
  return chosen ? canonical(chosen) : ''
}

function buildChoices(
  recs: SetupModelRecommendationsResponse,
  ns: NamespaceEntries,
): ModelChoices {
  return PICKERS.reduce<ModelChoices>(
    (acc, spec) => ({
      ...acc,
      [spec.key]: pickModel(
        valueOf(ns[spec.namespace], spec.settingKey),
        recommendedFor(recs, spec.key),
      ),
    }),
    perKey(''),
  )
}

interface LoadHandlers {
  setRecs: (value: SetupModelRecommendationsResponse) => void
  setModels: (value: ModelChoices) => void
  setError: (value: string) => void
  setLoading: (value: boolean) => void
}

async function loadModelSelection(
  token: CancellationToken,
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
    if (token.cancelled()) return
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
    if (!token.cancelled()) handlers.setError(getErrorMessage(caught))
  } finally {
    if (!token.cancelled()) handlers.setLoading(false)
  }
}

const EMPTY_MODELS: ModelChoices = perKey('')

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
  return useRef<Record<ModelKey, number>>(perKey(0))
}

function useWizardModelSelection(): ModelSelectionState {
  const [recs, setRecs] = useState<SetupModelRecommendationsResponse | null>(null)
  const [models, setModels] = useState<ModelChoices>(EMPTY_MODELS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const addToast = useToastStore((s) => s.add)
  const modelRequestIdsRef = useRequestIdRefs()

  useEffect(() => {
    const token = createCancellationToken()
    void loadModelSelection(token, {
      setRecs,
      setModels,
      setError,
      setLoading,
    })
    return token.cancel
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
          Prefilled with our recommendations, except the embedding model, which
          you choose. Override any of them now or later in Settings.
        </p>

        {visiblePickers.map((spec) => (
          <SelectField
            key={spec.key}
            label={spec.label}
            hint={hintFor(spec, models[spec.key])}
            value={models[spec.key]}
            onChange={(value) => {
              selectModel(spec, value)
            }}
            options={optionsFor(recs, spec)}
            placeholder={placeholderFor(spec)}
          />
        ))}
      </div>
    </SectionCard>
  )
}
