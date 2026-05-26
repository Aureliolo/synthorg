import { useEffect, useMemo, useState } from 'react'
import type { Node } from '@xyflow/react'
import type { AgentNodeData } from './build-org-tree'
import { getNodeLabel } from './node-utils'
import { OrgChartSearchOverlay } from './OrgChartSearchOverlay'

export interface OrgChartFilterResult {
  searchOpen: boolean
  searchMatchIds: Set<string> | null
  highlightedNodeIds: Set<string> | null
  overlay: React.ReactNode
}

/** True when a node's label or (for agents) role contains the query. */
function nodeMatchesQuery(n: Node, query: string): boolean {
  if (getNodeLabel(n).toLowerCase().includes(query)) return true
  if (n.type === 'agent' || n.type === 'ceo') {
    const role = (n.data as AgentNodeData).role?.toLowerCase() ?? ''
    return role.includes(query)
  }
  return false
}

/**
 * Command-palette-style search over the full org tree (including collapsed
 * departments), plus the dim-others highlight effect that's applied when
 * the overlay is open and has active matches.
 */
export function useOrgChartFilter(allNodes: Node[]): OrgChartFilterResult {
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        setSearchOpen(true)
      } else if (e.key === 'Escape' && searchOpen) {
        e.preventDefault()
        setSearchOpen(false)
        setSearchQuery('')
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [searchOpen])

  const normalisedQuery = searchOpen ? searchQuery.trim().toLowerCase() : ''

  const searchMatchIds = useMemo<Set<string> | null>(() => {
    if (!normalisedQuery) return null
    const matches = new Set<string>()
    for (const n of allNodes) {
      if (nodeMatchesQuery(n, normalisedQuery)) matches.add(n.id)
    }
    return matches
  }, [normalisedQuery, allNodes])

  const allNodeById = useMemo(() => {
    const map = new Map<string, Node>()
    for (const n of allNodes) map.set(n.id, n)
    return map
  }, [allNodes])

  const highlightedNodeIds = useMemo<Set<string> | null>(() => {
    if (!searchMatchIds) return null
    const expanded = new Set<string>(searchMatchIds)
    for (const id of searchMatchIds) {
      const node = allNodeById.get(id)
      if (node?.parentId) expanded.add(node.parentId)
    }
    return expanded
  }, [allNodeById, searchMatchIds])

  const overlay = (
    <OrgChartSearchOverlay
      open={searchOpen}
      query={searchQuery}
      onQueryChange={setSearchQuery}
      onClose={() => {
        setSearchOpen(false)
        setSearchQuery('')
      }}
      matchCount={searchMatchIds?.size ?? 0}
    />
  )

  return { searchOpen, searchMatchIds, highlightedNodeIds, overlay }
}
