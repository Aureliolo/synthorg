import type { LivingDocument } from '@/api/types'
import { DocBlockRenderer } from './DocBlockRenderer'

export interface DocViewerProps {
  doc: LivingDocument | null
  loading: boolean
  error: string | null
}

export function DocViewer({ doc, loading, error }: DocViewerProps) {
  if (loading && doc === null) {
    return (
      <div className="text-muted-foreground p-4 text-sm">
        Loading document...
      </div>
    )
  }
  if (error !== null && doc === null) {
    return (
      <div className="text-destructive p-4 text-sm">{error}</div>
    )
  }
  if (doc === null) {
    return (
      <div className="text-muted-foreground p-4 text-sm">
        Select a document from the list to view it.
      </div>
    )
  }
  return (
    <article className="flex flex-col gap-section-gap">
      <header className="border-border flex flex-col gap-1 border-b pb-3">
        <h1 className="text-2xl font-semibold">{doc.title}</h1>
        <p className="text-muted-foreground text-sm">
          {doc.doc_type.replace('_', ' ')} {'·'} authored by{' '}
          <code className="text-xs">{doc.author_agent_id}</code>
        </p>
        {doc.tags.length > 0 && (
          <ul className="flex flex-wrap gap-2">
            {doc.tags.map((tag) => (
              <li
                key={tag}
                className="border-border text-foreground/80 rounded-full border px-2 py-0.5 text-xs"
              >
                {tag}
              </li>
            ))}
          </ul>
        )}
      </header>
      <div className="flex flex-col gap-section-gap">
        {doc.body.map((block) => (
          <DocBlockRenderer key={block.block_id} block={block} />
        ))}
      </div>
    </article>
  )
}
