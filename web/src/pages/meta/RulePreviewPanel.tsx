import { motion } from 'motion/react'

import { InputField } from '@/components/ui/input-field'
import { cardEntrance } from '@/lib/motion'
import type { Comparator, PreviewResult } from '@/api/endpoints/custom-rules'

import { useRulePreview } from './useRulePreview'

const COMPARATOR_SYMBOLS: Record<string, string> = {
  lt: '<',
  le: '<=',
  gt: '>',
  ge: '>=',
  eq: '==',
  ne: '!=',
}

interface RulePreviewPanelProps {
  metricPath: string | null
  comparator: Comparator | null
  threshold: number
  metricLabel?: string | undefined
}

export function RulePreviewPanel({
  metricPath,
  comparator,
  threshold,
  metricLabel,
}: RulePreviewPanelProps) {
  const preview = useRulePreview(metricPath, comparator, threshold)

  if (!metricPath || !comparator) {
    return (
      <div className="rounded-lg border border-border bg-card/50 p-card text-body-sm text-muted-foreground">
        Select a metric and comparator to preview rule behavior.
      </div>
    )
  }

  const symbol = COMPARATOR_SYMBOLS[comparator] ?? comparator

  return (
    <motion.div
      variants={cardEntrance}
      initial="initial"
      animate="animate"
      className="space-y-3 rounded-lg border border-border bg-card/50 p-card"
    >
      <p className="text-body-sm text-muted-foreground">
        Fire when{' '}
        <span className="font-medium text-foreground">{metricLabel ?? metricPath}</span>{' '}
        <span className="font-mono text-accent">
          {symbol} {threshold}
        </span>
      </p>

      <InputField
        label="Test with sample value"
        type="number"
        value={preview.sampleValue}
        onChange={(e) => preview.setSampleValue(e.target.value)}
        placeholder="Enter a metric value to test"
        hint="The rule will be evaluated against this value"
      />

      {preview.error && <p className="text-body-sm text-danger">{preview.error}</p>}
      {preview.result && <RulePreviewResultBanner result={preview.result} />}
    </motion.div>
  )
}

interface RulePreviewResultBannerProps {
  result: PreviewResult
}

function RulePreviewResultBanner({ result }: RulePreviewResultBannerProps) {
  const className = result.would_fire
    ? 'rounded-md border border-warning/30 bg-warning/5 p-card text-body-sm text-warning'
    : 'rounded-md border border-success/30 bg-success/5 p-card text-body-sm text-success'
  const message = result.would_fire
    ? `Would fire: ${result.match?.description ?? 'Rule triggered'}`
    : 'Would NOT fire with this value.'
  return (
    <div role="status" aria-live="polite" className={className}>
      {message}
    </div>
  )
}
