import { useCallback, useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import {
  getDeliverableReceipt,
  validateDeliverableReceipt,
} from '@/api/endpoints/deliverableReceipts'
import type {
  DeliverableReceipt,
  ReceiptDecisionEntry,
  ReceiptSourceEntry,
  ReceiptTestEntry,
  ReceiptValidationResult,
} from '@/api/types'
import { Button } from '@/components/ui/button'
import { Collapsible } from '@/components/ui/collapsible'
import { MetadataGrid, type MetadataGridItem } from '@/components/ui/metadata-grid'
import { createLogger } from '@/lib/logger'
import { cn } from '@/lib/utils'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { getErrorMessage } from '@/utils/errors'
import { formatCurrency, formatDateTime } from '@/utils/format'

const log = createLogger('receipt-panel')

export interface ReceiptPanelProps {
  projectId: string
  slug: string
}

interface ReceiptState {
  receipt: DeliverableReceipt | null
  loading: boolean
  error: string | null
}

interface ReceiptFetchResult {
  key: string | null
  receipt: DeliverableReceipt | null
  error: string | null
}

function useDeliverableReceipt(projectId: string, slug: string): ReceiptState {
  const requestKey = `${projectId}::${slug}`
  const [result, setResult] = useState<ReceiptFetchResult>({
    key: null,
    receipt: null,
    error: null,
  })
  // Loading is derived: true until the fetch for the CURRENT key resolves.
  // This avoids a synchronous loading-reset setState inside the effect
  // (which the codebase forbids) while still showing a spinner whenever
  // projectId/slug change to a not-yet-fetched pair.
  const loading = result.key !== requestKey

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    const run = async (): Promise<void> => {
      try {
        const receipt = await getDeliverableReceipt(projectId, slug, controller.signal)
        if (!cancelled) setResult({ key: requestKey, receipt, error: null })
      } catch (err) {
        if (cancelled || controller.signal.aborted) return
        log.warn('Failed to load deliverable receipt:', getErrorMessage(err))
        setResult({ key: requestKey, receipt: null, error: getErrorMessage(err) })
      }
    }
    void run()
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [projectId, slug, requestKey])

  if (loading) return { receipt: null, loading: true, error: null }
  return { receipt: result.receipt, loading: false, error: result.error }
}

interface ValidationState {
  result: ReceiptValidationResult | null
  validating: boolean
  error: string | null
  validate: () => void
}

function useReceiptValidation(projectId: string, slug: string): ValidationState {
  const [result, setResult] = useState<ReceiptValidationResult | null>(null)
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const validate = useCallback(() => {
    setValidating(true)
    setError(null)
    const run = async (): Promise<void> => {
      try {
        setResult(await validateDeliverableReceipt(projectId, slug))
      } catch (err) {
        log.warn('Failed to validate receipt:', getErrorMessage(err))
        setError(getErrorMessage(err))
      } finally {
        setValidating(false)
      }
    }
    void run()
  }, [projectId, slug])

  return { result, validating, error, validate }
}

function ReceiptSummary({ receipt }: { receipt: DeliverableReceipt }) {
  const items: readonly MetadataGridItem[] = [
    {
      label: 'Cost',
      value: formatCurrency(receipt.total_cost, receipt.currency || DEFAULT_CURRENCY),
    },
    { label: 'Sources', value: String(receipt.sources.length) },
    { label: 'Decisions', value: String(receipt.decisions.length) },
    { label: 'Tests', value: String(receipt.tests.length) },
    {
      label: 'Red-team',
      value: receipt.red_team === null ? 'none' : receipt.red_team.verdict,
    },
    { label: 'Issued', value: formatDateTime(receipt.issued_at) },
  ]
  return <MetadataGrid items={items} columns={3} />
}

function SourceItem({ source }: { source: ReceiptSourceEntry }) {
  return (
    <li className="flex flex-col gap-0.5 border-border border-b pb-2 last:border-0 last:pb-0">
      <span className="text-foreground text-sm font-medium">{source.title}</span>
      <code className="text-text-muted text-xs">{source.uri}</code>
    </li>
  )
}

function DecisionItem({ decision }: { decision: ReceiptDecisionEntry }) {
  return (
    <li className="flex flex-col gap-0.5 border-border border-b pb-2 last:border-0 last:pb-0">
      <span className="text-foreground text-sm font-medium">{decision.title}</span>
      <span className="text-muted-foreground text-xs">{decision.rationale}</span>
    </li>
  )
}

function TestItem({ test }: { test: ReceiptTestEntry }) {
  const statusClass = test.passed ? 'text-success' : 'text-destructive'
  const statusLabel = test.timed_out ? 'timed out' : test.passed ? 'passed' : 'failed'
  return (
    <li className="flex items-center justify-between gap-3 border-border border-b pb-2 last:border-0 last:pb-0">
      <code className="text-text-muted truncate text-xs">{test.command}</code>
      <span className={cn('shrink-0 text-xs font-medium', statusClass)}>
        {statusLabel}
      </span>
    </li>
  )
}

function ReceiptSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-text-muted text-[10px] uppercase tracking-wide">{title}</h4>
      {children}
    </div>
  )
}

function ValidationRow({
  state,
  onValidate,
}: {
  state: ValidationState
  onValidate: () => void
}) {
  const { result, validating, error } = state
  return (
    <div className="flex items-center gap-3">
      <Button variant="outline" size="sm" onClick={onValidate} disabled={validating}>
        {validating ? 'Validating...' : 'Validate'}
      </Button>
      {error !== null && <span className="text-destructive text-xs">{error}</span>}
      {error === null && result !== null && (
        <span
          className={cn(
            'text-xs font-medium',
            result.valid ? 'text-success' : 'text-destructive',
          )}
        >
          {result.valid
            ? 'All present signals are consistent.'
            : `${result.errors.length} inconsistency(ies) found.`}
        </span>
      )}
    </div>
  )
}

function ReceiptBody({
  receipt,
  validation,
}: {
  receipt: DeliverableReceipt
  validation: ValidationState
}) {
  return (
    <div className="flex flex-col gap-section-gap">
      <ReceiptSummary receipt={receipt} />
      {receipt.sources.length > 0 && (
        <ReceiptSection title="Sources used">
          <ul className="flex flex-col gap-2">
            {receipt.sources.map((source) => (
              <SourceItem key={source.source_id} source={source} />
            ))}
          </ul>
        </ReceiptSection>
      )}
      {receipt.decisions.length > 0 && (
        <ReceiptSection title="Key decisions">
          <ul className="flex flex-col gap-2">
            {receipt.decisions.map((decision) => (
              <DecisionItem key={decision.entry_id} decision={decision} />
            ))}
          </ul>
        </ReceiptSection>
      )}
      {receipt.tests.length > 0 && (
        <ReceiptSection title="Tests run">
          <ul className="flex flex-col gap-2">
            {receipt.tests.map((test) => (
              <TestItem key={test.record_id} test={test} />
            ))}
          </ul>
        </ReceiptSection>
      )}
      {receipt.cassette !== null && (
        <ReceiptSection title="Replayable cassette">
          <code className="text-text-muted break-all text-xs">
            {receipt.cassette.path}
          </code>
        </ReceiptSection>
      )}
      <ValidationRow state={validation} onValidate={validation.validate} />
    </div>
  )
}

/**
 * Collapsible "Provenance Receipt" panel for a deliverable document.
 *
 * Fetches the receipt on mount and renders the six provenance signals
 * (sources, decisions, cost, tests, red-team, cassette) with a Validate
 * action. Absent or not-yet-built receipts degrade to a muted note.
 */
export function ReceiptPanel({ projectId, slug }: ReceiptPanelProps) {
  const { receipt, loading, error } = useDeliverableReceipt(projectId, slug)
  const validation = useReceiptValidation(projectId, slug)

  const title = (
    <span className="flex items-center gap-2">
      <ShieldCheck aria-hidden="true" className="size-4 shrink-0" />
      Provenance Receipt
    </span>
  )

  return (
    <Collapsible title={title} defaultOpen={false}>
      {loading && (
        <p className="text-muted-foreground text-sm">Loading receipt...</p>
      )}
      {!loading && receipt === null && (
        <p className="text-muted-foreground text-sm">
          {error ?? 'No provenance receipt recorded for this deliverable yet.'}
        </p>
      )}
      {!loading && receipt !== null && (
        <ReceiptBody receipt={receipt} validation={validation} />
      )}
    </Collapsible>
  )
}
