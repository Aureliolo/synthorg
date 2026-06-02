import type {
  BulletListBlock,
  CodeBlock,
  DecisionBlock,
  HeadingBlock,
  LinkBlock,
  MetricBlock,
  ProseBlock,
} from '@/api/types'

type DocBlock =
  | HeadingBlock
  | ProseBlock
  | BulletListBlock
  | CodeBlock
  | DecisionBlock
  | MetricBlock
  | LinkBlock

function HeadingBlockView({ block }: { block: HeadingBlock }) {
  const { text } = block
  if (block.level === 1) return <h1 className="text-2xl font-semibold">{text}</h1>
  if (block.level === 2) return <h2 className="text-xl font-semibold">{text}</h2>
  if (block.level === 3) return <h3 className="text-lg font-semibold">{text}</h3>
  return <h4 className="text-base font-semibold">{text}</h4>
}

function ProseBlockView({ block }: { block: ProseBlock }) {
  return (
    <p className="text-foreground/90 whitespace-pre-wrap leading-relaxed">{block.text}</p>
  )
}

function BulletListBlockView({ block }: { block: BulletListBlock }) {
  return (
    <ul className="text-foreground/90 list-inside list-disc space-y-1">
      {block.items.map((item, idx) => (
        <li key={`${block.block_id}-${String(idx)}`}>{item}</li>
      ))}
    </ul>
  )
}

function CodeBlockView({ block }: { block: CodeBlock }) {
  return (
    <pre className="border-border bg-muted/40 overflow-x-auto rounded border p-card">
      <code className="font-mono text-sm">
        {block.language ? `[${block.language}]\n` : ''}
        {block.code}
      </code>
    </pre>
  )
}

function DecisionBlockView({ block }: { block: DecisionBlock }) {
  return (
    <div className="border-primary/40 bg-primary/5 rounded border-l-4 p-card">
      <p className="text-foreground font-medium">Decision: {block.decision}</p>
      <p className="text-muted-foreground mt-1 text-sm">Rationale: {block.rationale}</p>
    </div>
  )
}

function MetricBlockView({ block }: { block: MetricBlock }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-muted-foreground text-sm">{block.name}:</span>
      <span className="text-foreground text-base font-semibold">
        {block.value}
        {block.unit ? ` ${block.unit}` : ''}
      </span>
    </div>
  )
}

const ALLOWED_LINK_SCHEMES = new Set(['http:', 'https:', 'mailto:'])

// Defense-in-depth: the backend LinkBlock validator already rejects
// dangerous schemes at write time, but guard the render path too so a
// disallowed (javascript:, data:) or protocol-relative (//host) url can
// never reach the href attribute. Relative paths and #fragments pass.
function sanitizeHref(url: string): string {
  if (url.startsWith('//')) return '#'
  try {
    return ALLOWED_LINK_SCHEMES.has(new URL(url).protocol) ? url : '#'
  } catch {
    return url
  }
}

function LinkBlockView({ block }: { block: LinkBlock }) {
  return (
    <a
      href={sanitizeHref(block.url)}
      className="text-primary underline hover:no-underline"
      rel="noopener noreferrer"
      target="_blank"
    >
      {block.label}
    </a>
  )
}

/**
 * Single living-document block renderer.
 *
 * One sub-component per known ``block_kind``, dispatched via a
 * table-driven lookup. Unknown kinds render as a neutral fallback so a
 * future block type added on the backend does not crash the wiki view;
 * the dashboard surfaces it as plain text until a renderer is added.
 */
export interface DocBlockRendererProps {
  block: DocBlock
}

export function DocBlockRenderer({ block }: DocBlockRendererProps) {
  switch (block.block_kind) {
    case 'heading':
      return <HeadingBlockView block={block} />
    case 'prose':
      return <ProseBlockView block={block} />
    case 'bullet_list':
      return <BulletListBlockView block={block} />
    case 'code':
      return <CodeBlockView block={block} />
    case 'decision':
      return <DecisionBlockView block={block} />
    case 'metric':
      return <MetricBlockView block={block} />
    case 'link':
      return <LinkBlockView block={block} />
    default: {
      const unknownBlock = block as { block_kind: string }
      return (
        <p className="text-muted-foreground text-sm italic">
          [Unsupported block: {unknownBlock.block_kind}]
        </p>
      )
    }
  }
}
