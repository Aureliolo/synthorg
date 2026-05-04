package scaffold

import (
	"strings"
	"testing"
)

func TestRenderControllerShape(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindController, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	wantPaths := []string{
		"src/synthorg/api/controllers/widget.py",
		"src/synthorg/api/services/widget_service.py",
		"tests/unit/api/controllers/test_widget.py",
		"src/synthorg/api/controllers/widget_WIRING.md",
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

func TestControllerConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindController, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "src/synthorg/api/controllers/widget.py")

	// Service-injected via _service factory (per CLAUDE.md persistence-boundary rule).
	mustContain(t, body, "def _service(state: State) -> WidgetService:")
	mustContain(t, body, "class WidgetController(Controller):")

	// Domain-error usage: NotFoundError from synthorg.core.domain_errors,
	// not bare Exception or RuntimeError.
	mustContain(t, body, "from synthorg.core.domain_errors import NotFoundError")
	mustContain(t, body, "raise NotFoundError(msg)")
	if strings.Contains(body, "raise Exception") || strings.Contains(body, "raise RuntimeError") {
		t.Error("controller must not raise bare Exception / RuntimeError (domain-error gate)")
	}

	// Guards: read endpoints use require_read_access, write endpoints require_write_access.
	mustContain(t, body, "require_read_access")
	mustContain(t, body, "require_write_access")
}

func TestControllerServiceConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindController, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "src/synthorg/api/services/widget_service.py")

	// Service wraps the protocol, never the impl.
	mustContain(t, body, "from synthorg.persistence.widget_protocol import (")
	mustContain(t, body, "WidgetRepository,")

	// Service is a thin facade -- no direct sqlite/psycopg imports.
	if strings.Contains(body, "import aiosqlite") || strings.Contains(body, "import psycopg") {
		t.Error("service layer must not import DB drivers (persistence-boundary rule)")
	}
}

func TestControllerTestConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindController, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "tests/unit/api/controllers/test_widget.py")

	mustContain(t, body, "pytestmark = pytest.mark.unit")
	mustContain(t, body, "MagicMock(spec=")
	if strings.Contains(body, "MagicMock()") || strings.Contains(body, "AsyncMock()") {
		t.Error("test scaffold contains a Mock/AsyncMock/MagicMock without spec= (mock-spec gate fails)")
	}
}
