package scaffold

import (
	"strings"
	"testing"
)

func TestRenderToolShape(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindTool, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	wantPaths := []string{
		"src/synthorg/meta/mcp/handlers/widget.py",
		"tests/unit/meta/mcp/handlers/test_widget.py",
		"src/synthorg/meta/mcp/handlers/widget_WIRING.md",
	}
	if len(files) != len(wantPaths) {
		t.Fatalf("rendered %d files, want %d", len(files), len(wantPaths))
	}
	got := make(map[string]bool, len(files))
	for _, f := range files {
		got[f.Path] = true
	}
	for _, want := range wantPaths {
		if !got[want] {
			t.Errorf("missing rendered file %q", want)
		}
	}
}

func TestToolHandlerConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindTool, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "src/synthorg/meta/mcp/handlers/widget.py")

	// Typed boundary: parse_typed with hardcoded LiteralString label.
	mustContain(t, body, `parse_typed(`)
	mustContain(t, body, `"mcp.tool"`)

	// Args model inherits PaginationFields mixin (frozen + extra=forbid).
	mustContain(t, body, "from synthorg.meta.mcp.domains._common_args import PaginationFields")
	mustContain(t, body, "class WidgetListArgs(PaginationFields):")

	// common_logging helpers for the two log paths exercised here.
	mustContain(t, body, "log_handler_argument_invalid")
	mustContain(t, body, "log_handler_invoke_failed")

	// Success path: structured log via event constant.
	mustContain(t, body, "MCP_HANDLER_INVOKE_SUCCESS")

	// Envelope helpers: ok / err.
	mustContain(t, body, "return ok(")
	mustContain(t, body, "return err(")

	// Non-recoverable errors propagate.
	mustContain(t, body, "except (MemoryError, RecursionError):")
	mustContain(t, body, "raise")

	if strings.Contains(body, "from __future__") {
		t.Error("handler must not contain `from __future__ import annotations`")
	}
}

func TestToolTestConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindTool, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "tests/unit/meta/mcp/handlers/test_widget.py")

	mustContain(t, body, "pytestmark = pytest.mark.unit")

	// Mock spec gate: every Mock / AsyncMock / MagicMock declares a spec.
	// The scaffold uses local Protocol stubs (_AppState, _<ClassName>Service)
	// so spec= has a concrete attribute surface to enforce; the user replaces
	// these with real types once the service module exists.
	mustContain(t, body, "MagicMock(spec=")
	mustContain(t, body, "AsyncMock(")
	if strings.Contains(body, "MagicMock()") || strings.Contains(body, "AsyncMock()") {
		t.Error("test scaffold contains a Mock() / AsyncMock() / MagicMock() without spec= argument (mock-spec gate fails)")
	}
}
