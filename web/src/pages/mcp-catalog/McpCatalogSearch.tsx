import { InputField } from '@/components/ui/input-field'
import { useMcpCatalogStore } from '@/stores/mcp-catalog'

export function McpCatalogSearch() {
  const searchQuery = useMcpCatalogStore((s) => s.searchQuery)
  const setSearchQuery = useMcpCatalogStore((s) => s.setSearchQuery)

  return (
    <div className="w-64">
      <InputField
        label="Search"
        placeholder="Search MCP catalog..."
        value={searchQuery}
        onValueChange={(v) => {
          setSearchQuery(v)
        }}
      />
    </div>
  )
}
