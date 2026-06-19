/**
 * Experiment registry explorer.
 *
 * The experiment registry is keyed by experiment name (there is no
 * list-all endpoint), so the operator enters a key to inspect its
 * registered variants and recorded assignment history, and can register a
 * new variant against the loaded experiment. Assignment remains a runtime
 * agent-only write; see the experiments controller docstring.
 */
import { useCallback, useRef, useState } from 'react'
import { FlaskConical, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonText } from '@/components/ui/skeleton'
import { listAssignments, listVariants, registerVariant } from '@/api/endpoints/experiments'
import type { ExperimentAssignment, ExperimentVariant } from '@/api/types'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { sanitizeForLog } from '@/utils/logging'
import { formatDateTime, formatNumber } from '@/utils/format'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('ExperimentExplorer')

interface ExperimentData {
  variants: readonly ExperimentVariant[]
  assignments: readonly ExperimentAssignment[]
  loading: boolean
  error: string | null
  loaded: boolean
}

const EMPTY: ExperimentData = {
  variants: [],
  assignments: [],
  loading: false,
  error: null,
  loaded: false,
}

function VariantsTable({ variants }: { variants: readonly ExperimentVariant[] }) {
  return (
    <table className="w-full min-w-[28rem] text-sm">
      <thead>
        <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
          <th className="py-2 pr-4 font-medium">Variant</th>
          <th className="py-2 pr-4 text-right font-medium">Weight</th>
          <th className="py-2 font-medium">Description</th>
        </tr>
      </thead>
      <tbody>
        {variants.map((v) => (
          <tr key={v.variant} className="border-t border-border">
            <td className="py-2 pr-4 font-medium text-foreground">{v.variant}</td>
            <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(v.weight)}</td>
            <td className="py-2 text-muted-foreground">{v.description || '--'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function AssignmentsTable({ assignments }: { assignments: readonly ExperimentAssignment[] }) {
  return (
    <table className="w-full min-w-[28rem] text-sm">
      <thead>
        <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
          <th className="py-2 pr-4 font-medium">Subject</th>
          <th className="py-2 pr-4 font-medium">Variant</th>
          <th className="py-2 font-medium">Assigned</th>
        </tr>
      </thead>
      <tbody>
        {assignments.map((a) => (
          <tr key={`${a.subject_id}-${a.assigned_at}`} className="border-t border-border">
            <td className="py-2 pr-4 font-mono text-xs text-foreground">{a.subject_id}</td>
            <td className="py-2 pr-4 text-foreground">{a.variant}</td>
            <td className="py-2 text-xs text-muted-foreground">{formatDateTime(a.assigned_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ExperimentResults({ data }: { data: ExperimentData }) {
  if (data.loading) return <SkeletonText lines={4} />
  if (data.error) {
    return (
      <ErrorBanner
        severity="warning"
        title="Could not load experiment"
        description={data.error}
      />
    )
  }
  if (!data.loaded) return null
  if (data.variants.length === 0 && data.assignments.length === 0) {
    return (
      <EmptyState
        icon={FlaskConical}
        title="No data for this experiment"
        description="No variants or assignments are registered under that key."
      />
    )
  }
  return (
    <div className="space-y-section-gap">
      {data.variants.length > 0 && (
        <div className="overflow-x-auto">
          <VariantsTable variants={data.variants} />
        </div>
      )}
      {data.assignments.length > 0 && (
        <div className="overflow-x-auto">
          <AssignmentsTable assignments={data.assignments} />
        </div>
      )}
    </div>
  )
}

interface VariantRegisterFormProps {
  experiment: string
  onRegistered: () => void
}

const DEFAULT_VARIANT_WEIGHT = 1

function VariantRegisterForm({ experiment, onRegistered }: VariantRegisterFormProps) {
  const [variant, setVariant] = useState('')
  const [weight, setWeight] = useState(String(DEFAULT_VARIANT_WEIGHT))
  const [description, setDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const parsedWeight = Number(weight)
  const weightValid = Number.isInteger(parsedWeight) && parsedWeight >= 1
  const valid = variant.trim() !== '' && weightValid

  const handleSubmit = async () => {
    if (!valid || submitting) return
    setSubmitting(true)
    try {
      await registerVariant(experiment, {
        variant: variant.trim(),
        weight: parsedWeight,
        ...(description.trim() ? { description: description.trim() } : {}),
      })
      useToastStore.getState().add({ variant: 'success', title: 'Variant registered' })
      setVariant('')
      setWeight(String(DEFAULT_VARIANT_WEIGHT))
      setDescription('')
      onRegistered()
    } catch (err) {
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Could not register variant'),
        description: getErrorMessage(err),
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <SectionCard title="Register variant" icon={Plus}>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void handleSubmit()
        }}
        className="space-y-4"
      >
        <div className="grid grid-cols-[1fr_8rem] gap-grid-gap max-[479px]:grid-cols-1">
          <InputField
            label="Variant name"
            value={variant}
            onValueChange={setVariant}
            required
            placeholder="e.g. high-temperature"
          />
          <InputField
            label="Weight"
            type="number"
            value={weight}
            onValueChange={setWeight}
            error={weightValid ? undefined : 'Weight must be a positive integer.'}
          />
        </div>
        <InputField
          label="Description"
          value={description}
          onValueChange={setDescription}
          hint="Optional operator notes"
        />
        <div className="flex justify-end">
          <Button type="submit" disabled={!valid || submitting}>
            Register variant
          </Button>
        </div>
      </form>
    </SectionCard>
  )
}

export function ExperimentExplorer() {
  const [key, setKey] = useState('')
  const [loadedKey, setLoadedKey] = useState('')
  const [data, setData] = useState<ExperimentData>(EMPTY)
  // Monotonic request token: a slow earlier load must not overwrite the
  // display once a newer load has been issued for a different key.
  const requestSeqRef = useRef(0)

  const load = useCallback((experiment: string) => {
    const trimmed = experiment.trim()
    if (trimmed === '') return
    setLoadedKey(trimmed)
    const seq = requestSeqRef.current + 1
    requestSeqRef.current = seq
    setData({ ...EMPTY, loading: true })
    void Promise.allSettled([listVariants(trimmed), listAssignments(trimmed)]).then(
      ([variantsResult, assignmentsResult]) => {
        if (seq !== requestSeqRef.current) return
        const variants = variantsResult.status === 'fulfilled' ? variantsResult.value : []
        const assignments =
          assignmentsResult.status === 'fulfilled' ? assignmentsResult.value : []
        // Only surface a hard error when BOTH calls fail; if one
        // succeeds, show its data (e.g. a new experiment with variants
        // but no assignments yet). Log a single-side failure so it is
        // not lost.
        if (
          variantsResult.status === 'rejected'
          && assignmentsResult.status === 'rejected'
        ) {
          const message = getErrorMessage(variantsResult.reason)
          log.error('experiment load failed', { error: sanitizeForLog(message) })
          setData({ ...EMPTY, error: message, loaded: true })
          return
        }
        const partial = [variantsResult, assignmentsResult].find(
          (r) => r.status === 'rejected',
        )
        if (partial?.status === 'rejected') {
          log.warn('experiment partial load failure', {
            error: sanitizeForLog(getErrorMessage(partial.reason)),
          })
        }
        setData({ variants, assignments, loading: false, error: null, loaded: true })
      },
    )
  }, [])

  return (
    <form
      className="space-y-section-gap"
      onSubmit={(e) => {
        e.preventDefault()
        load(key)
      }}
    >
      <div className="flex items-end gap-grid-gap">
        <div className="flex-1">
          <InputField
            label="Experiment key"
            value={key}
            onChange={(e) => setKey(e.currentTarget.value)}
            placeholder="e.g. planner-temperature"
          />
        </div>
        <Button type="submit" disabled={key.trim() === '' || data.loading}>
          {data.loading ? 'Loading...' : 'View'}
        </Button>
      </div>
      <ExperimentResults data={data} />
      {data.loaded && loadedKey !== '' && (
        <VariantRegisterForm experiment={loadedKey} onRegistered={() => load(loadedKey)} />
      )}
    </form>
  )
}
