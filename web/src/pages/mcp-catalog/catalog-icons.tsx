import { createElement } from 'react'
import {
  Brain,
  Database,
  Folder,
  GitBranch,
  Globe,
  MessageSquare,
  type LucideIcon,
  type LucideProps,
  Package,
  Search,
} from 'lucide-react'

/**
 * Map bundled MCP catalog entry ids to lucide-react icons.
 *
 * Unknown ids fall back to a generic ``Package`` icon so new catalog
 * entries render without crashing until an icon is added here.
 */
const ENTRY_ICONS: Record<string, LucideIcon> = {
  'github-mcp': GitBranch,
  'slack-mcp': MessageSquare,
  'filesystem-mcp': Folder,
  'postgres-mcp': Database,
  'sqlite-mcp': Database,
  'brave-search-mcp': Search,
  'puppeteer-mcp': Globe,
  'memory-mcp': Brain,
}

export interface CatalogEntryIconProps extends LucideProps {
  entryId: string
}

/**
 * Render the Lucide icon for an MCP catalog entry.
 *
 * The lookup happens inside this component (not at the call site) so the
 * ``react-x/static-components`` rule sees a stable component reference at
 * every JSX usage.
 */
export function CatalogEntryIcon({ entryId, ...rest }: CatalogEntryIconProps) {
  return createElement(ENTRY_ICONS[entryId] ?? Package, rest)
}
