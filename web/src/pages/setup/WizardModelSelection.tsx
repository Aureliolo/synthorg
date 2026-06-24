import { useCallback, useEffect, useState } from 'react'
import { getModelRecommendations } from '@/api/endpoints/setup'
import { getNamespaceSettings, updateSetting } from '@/api/endpoints/settings'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { Skeleton } from '@/components/ui/skeleton'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import type { SettingEntry } from '@/api/types/settings'
import type { SetupModelRecommendationsResponse } from '@/api/types/setup'

const DECOMPOSITION_KEY = 'decomposition_model'
const EMBEDDING_KEY = 'embedder_model'

function toOptions(ids: readonly string[]): readonly SelectOption[] {
  return ids.map((id) => ({ value: id, label: id }))
}

function valueOf(entries: readonly SettingEntry[], key: string): string | undefined {
  const found = entries.find((entry) => entry.definition.key === key)
  return found && found.value ? found.value : undefined
}

interface ModelChoices {
  decomposition: string
  embedding: string
}

interface ModelSelectionState {
  recs: SetupModelRecommendationsResponse | null
  choices: ModelChoices
  loading: boolean
  error: string | null
  selectDecomposition: (value: string) => void
  selectEmbedding: (value: string) => void
}

function useWizardModelSelection(): ModelSelectionState {
  const [recs, setRecs] = useState<SetupModelRecommendationsResponse | null>(null)
  const [choices, setChoices] = useState<ModelChoices>({ decomposition: '', embedding: '' })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const addToast = useToastStore((s) => s.add)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [recommendations, coordination, memory] = await Promise.all([
          getModelRecommendations(),
          getNamespaceSettings('coordination'),
          getNamespaceSettings('memory'),
        ])
        if (cancelled) return
        setRecs(recommendations)
        // Prefill from the persisted value when the operator has already
        // chosen one; otherwise fall back to the backend recommendation.
        setChoices({
          decomposition:
            valueOf(coordination, DECOMPOSITION_KEY) ??
            recommendations.decomposition_recommended ??
            '',
          embedding:
            valueOf(memory, EMBEDDING_KEY) ?? recommendations.embedding_recommended ?? '',
        })
      } catch (caught) {
        if (!cancelled) setError(getErrorMessage(caught))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const selectDecomposition = useCallback(
    (value: string) => {
      setChoices((prev) => ({ ...prev, decomposition: value }))
      void updateSetting('coordination', DECOMPOSITION_KEY, { value }).catch((caught) => {
        addToast({
          variant: 'error',
          title: 'Could not save the coordination model',
          description: getErrorMessage(caught),
        })
      })
    },
    [addToast],
  )

  const selectEmbedding = useCallback(
    (value: string) => {
      setChoices((prev) => ({ ...prev, embedding: value }))
      // Persist only the model id; setup completion resolves the matching
      // embedder_dims for the chosen model, and ingest captures the real
      // dimensions if they differ.
      void updateSetting('memory', EMBEDDING_KEY, { value }).catch((caught) => {
        addToast({
          variant: 'error',
          title: 'Could not save the embedding model',
          description: getErrorMessage(caught),
        })
      })
    },
    [addToast],
  )

  return { recs, choices, loading, error, selectDecomposition, selectEmbedding }
}

/**
 * Coordinator + memory model pickers for the final wizard step.
 *
 * Surfaces the two models the operator was previously never shown: the
 * coordinator's decomposition model and the memory embedding model. Both are
 * prefilled with a sensible recommendation (the most senior agent's model and
 * the best-ranked embedder in the catalogue) and overridable from the full
 * candidate lists. Each change writes straight through the settings API, so
 * the choice survives independently of the wizard and is editable later in
 * Settings.
 */
export function WizardModelSelection() {
  const { recs, choices, loading, error, selectDecomposition, selectEmbedding } =
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

  const embeddingDims = recs.embedding_recommended_dims
  return (
    <section className="space-y-4 rounded-lg border border-border bg-card p-card">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-foreground">Coordination &amp; memory models</h3>
        <p className="text-xs text-muted-foreground">
          Prefilled with our recommendations. Override either now or later in Settings.
        </p>
      </div>

      <SelectField
        label="Coordination model"
        hint="Used by the coordinator to break briefs into tasks."
        value={choices.decomposition}
        onChange={selectDecomposition}
        options={toOptions(recs.decomposition_candidates)}
      />

      <SelectField
        label="Embedding model"
        hint={
          embeddingDims
            ? `Powers agent memory search. Recommended at ${embeddingDims} dimensions.`
            : 'Powers agent memory search.'
        }
        value={choices.embedding}
        onChange={selectEmbedding}
        options={toOptions(recs.embedding_candidates)}
      />
    </section>
  )
}
