package scaffold

import "fmt"

// renderTool is implemented in a follow-up commit on this PR;
// dispatch is wired now so the Cobra subcommand graph is complete.
//
// The intended layout is:
//
//   - src/synthorg/meta/mcp/handlers/<domain>.py (handler with args_model + parse_typed + common_logging)
//   - tests/unit/meta/mcp/handlers/test_<domain>.py
//   - WIRING.md (registration in meta/mcp/domains/<domain>.py)
func renderTool(_ Params) ([]RenderedFile, error) {
	return nil, fmt.Errorf("scaffold kind %q is not yet implemented", KindTool)
}
