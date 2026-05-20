import type { DocBlock } from '@/api/endpoints/projectDocs'

/**
 * Single living-document block renderer.
 *
 * One JSX branch per known ``block_kind``. Unknown kinds render as a
 * neutral fallback so a future block type added on the backend does
 * not crash the wiki view; the dashboard surfaces it as plain text
 * until a renderer is added.
 */
export interface DocBlockRendererProps {
  block: DocBlock
}

export function DocBlockRenderer({ block }: DocBlockRendererProps) {
  switch (block.block_kind) {
    case 'heading': {
      const level = Number(block.level) || 2
      const text = String(block.text ?? '')
      if (level === 1) return <h1 className="text-2xl font-semibold">{text}</h1>
      if (level === 2) return <h2 className="text-xl font-semibold">{text}</h2>
      if (level === 3) return <h3 className="text-lg font-semibold">{text}</h3>
      return <h4 className="text-base font-semibold">{text}</h4>
    }
    case 'prose': {
      return (
        <p className="text-foreground/90 whitespace-pre-wrap leading-relaxed">
          {String(block.text ?? '')}
        </p>
      )
    }
    case 'bullet_list': {
      const items = (block.items as readonly string[] | undefined) ?? []
      return (
        <ul className="text-foreground/90 list-inside list-disc space-y-1">
          {items.map((item, idx) => (
            <li key={`${block.block_id}-${String(idx)}`}>{item}</li>
          ))}
        </ul>
      )
    }
    case 'code': {
      const code = String(block.code ?? '')
      const language = block.language ? String(block.language) : null
      return (
        <pre className="border-border bg-muted/40 overflow-x-auto rounded border p-3">
          <code className="font-mono text-sm">
            {language ? `[${language}]\n` : ''}
            {code}
          </code>
        </pre>
      )
    }
    case 'decision': {
      const decision = String(block.decision ?? '')
      const rationale = String(block.rationale ?? '')
      return (
        <div className="border-primary/40 bg-primary/5 rounded border-l-4 p-3">
          <p className="text-foreground font-medium">Decision: {decision}</p>
          <p className="text-muted-foreground mt-1 text-sm">Rationale: {rationale}</p>
        </div>
      )
    }
    case 'metric': {
      const name = String(block.name ?? '')
      const value = String(block.value ?? '')
      const unit = block.unit ? String(block.unit) : ''
      return (
        <div className="flex items-baseline gap-2">
          <span className="text-muted-foreground text-sm">{name}:</span>
          <span className="text-foreground text-base font-semibold">
            {value}
            {unit ? ` ${unit}` : ''}
          </span>
        </div>
      )
    }
    case 'link': {
      const label = String(block.label ?? '')
      const url = String(block.url ?? '')
      return (
        <a
          href={url}
          className="text-primary underline hover:no-underline"
          rel="noopener noreferrer"
          target="_blank"
        >
          {label}
        </a>
      )
    }
    default: {
      return (
        <p className="text-muted-foreground text-sm italic">
          [Unsupported block: {String(block.block_kind)}]
        </p>
      )
    }
  }
}
