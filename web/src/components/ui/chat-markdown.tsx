import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'

import { cn } from '@/lib/utils'

export interface ChatMarkdownProps {
  /** Raw markdown text from an assistant or agent turn. */
  content: string
  className?: string
}

// Every markdown element is styled through descendant selectors so the
// rendered tree carries only design-system tokens (no per-element renderer,
// so no raw HTML ever reaches the DOM). Tables and code blocks scroll inside
// themselves so a wide block never scrolls the whole message.
const MARKDOWN_PROSE = cn(
  'space-y-2 text-sm leading-relaxed text-foreground break-words',
  '[&_p]:whitespace-pre-wrap',
  '[&_ul]:ml-4 [&_ul]:list-disc [&_ul]:space-y-1',
  '[&_ol]:ml-4 [&_ol]:list-decimal [&_ol]:space-y-1',
  '[&_li]:pl-1',
  '[&_h1]:text-base [&_h1]:font-semibold',
  '[&_h2]:text-sm [&_h2]:font-semibold',
  '[&_h3]:text-sm [&_h3]:font-semibold',
  '[&_strong]:font-semibold [&_em]:italic',
  '[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2',
  '[&_blockquote]:border-l-2 [&_blockquote]:border-border',
  '[&_blockquote]:pl-3 [&_blockquote]:text-text-secondary',
  '[&_code]:rounded [&_code]:bg-card-hover [&_code]:px-1 [&_code]:py-0.5',
  '[&_code]:font-mono [&_code]:text-xs',
  '[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-card-hover',
  '[&_pre]:p-card [&_pre]:font-mono [&_pre]:text-xs',
  '[&_table]:block [&_table]:w-full [&_table]:overflow-x-auto',
  '[&_table]:border-collapse [&_table]:text-xs',
  '[&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left',
  '[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1',
)

/**
 * Render an assistant/agent message as sanitised markdown. ``rehypeSanitize``
 * strips any HTML the model emits (scripts, event handlers, unknown tags), so
 * untrusted model output can never inject markup; ``remarkGfm`` adds tables,
 * task lists, and autolinks. Styling rides on descendant selectors of the
 * design-system token classes above.
 */
export function ChatMarkdown({ content, className }: ChatMarkdownProps) {
  return (
    <div className={cn(MARKDOWN_PROSE, className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
