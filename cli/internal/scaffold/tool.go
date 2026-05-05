package scaffold

import "fmt"

// renderTool emits a paginated MCP-handler skeleton:
//
//   - src/synthorg/meta/mcp/handlers/<domain>.py            -- _list handler with args_model + parse_typed + common_logging
//   - tests/unit/meta/mcp/handlers/test_<domain>.py         -- args validation + ok/err envelope coverage
//   - src/synthorg/meta/mcp/handlers/<domain>_WIRING.md     -- registration in meta/mcp/domains/
//
// The handler imports `from synthorg.meta.mcp.domains._common_args import
// PaginationFields` so the args model inherits the project-wide
// frozen + extra=forbid + bounds-validated mixin without rebuilding it.
// `parse_typed("mcp.tool", ...)` covers the typed-boundary contract
// (scripts/check_boundary_typed.py); the three common_logging helpers
// cover the structured log paths required by the MCP handler contract.
func renderTool(p Params) ([]RenderedFile, error) {
	files := []struct {
		out string
		tpl string
	}{
		{
			fmt.Sprintf("src/synthorg/meta/mcp/handlers/%s.py", p.Domain),
			"tool_handler.py.tmpl",
		},
		{
			fmt.Sprintf("tests/unit/meta/mcp/handlers/test_%s.py", p.Domain),
			"tool_test.py.tmpl",
		},
		{
			fmt.Sprintf("src/synthorg/meta/mcp/handlers/%s_WIRING.md", p.Domain),
			"tool_wiring.md.tmpl",
		},
	}
	out := make([]RenderedFile, 0, len(files))
	for _, f := range files {
		body, err := renderTemplate(f.tpl, p)
		if err != nil {
			return nil, err
		}
		out = append(out, RenderedFile{Path: f.out, Contents: body})
	}
	return out, nil
}
