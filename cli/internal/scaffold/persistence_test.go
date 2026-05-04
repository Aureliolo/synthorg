package scaffold

import (
	"strings"
	"testing"
)

func TestRenderPersistenceShape(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindPersistence, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	wantPaths := []string{
		"src/synthorg/widget/models.py",
		"src/synthorg/persistence/widget_protocol.py",
		"src/synthorg/persistence/sqlite/widget_repo.py",
		"src/synthorg/persistence/postgres/widget_repo.py",
		"src/synthorg/observability/events/widget.py",
		"tests/conformance/persistence/test_widget_repository.py",
		"src/synthorg/persistence/widget_WIRING.md",
	}
	if len(files) != len(wantPaths) {
		t.Fatalf("rendered %d files, want %d", len(files), len(wantPaths))
	}
	got := make(map[string]string, len(files))
	for _, f := range files {
		got[f.Path] = string(f.Contents)
	}
	for _, want := range wantPaths {
		if _, ok := got[want]; !ok {
			t.Errorf("missing rendered file %q", want)
		}
	}
}

func TestPersistenceProtocolConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindPersistence, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "src/synthorg/persistence/widget_protocol.py")

	// CLAUDE.md repository CRUD: save/get/list_items/delete on the
	// Protocol, list returning a tuple.
	mustContain(t, body, "@runtime_checkable")
	mustContain(t, body, "class WidgetRepository(Protocol):")
	mustContain(t, body, "async def save(self, item: WidgetItem) -> None:")
	mustContain(t, body, "async def get(self, item_id: NotBlankStr) -> WidgetItem | None:")
	mustContain(t, body, "tuple[WidgetItem, ...]")
	mustContain(t, body, "async def delete(self, item_id: NotBlankStr) -> bool:")
}

func TestPersistenceSQLiteRepoConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindPersistence, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "src/synthorg/persistence/sqlite/widget_repo.py")

	// Persistence boundary: aiosqlite imports allowed under persistence/.
	mustContain(t, body, "import aiosqlite")
	mustContain(t, body, "from synthorg.core.persistence_errors import ConstraintViolationError, QueryError")

	// Secret-log redaction: error_type + safe_error_description, never error=str(exc).
	mustContain(t, body, "error_type=type(exc).__name__")
	mustContain(t, body, "error=safe_error_description(exc)")
	if strings.Contains(body, "error=str(exc)") {
		t.Error("sqlite repo must not call logger with error=str(exc) (secret-log redaction gate)")
	}

	// Event constants imported from synthorg.observability.events.<domain>.
	mustContain(t, body, "from synthorg.observability.events.widget import")
	mustContain(t, body, "WIDGET_REPO_FAILED")
	mustContain(t, body, "WIDGET_REPO_FETCHED")
	mustContain(t, body, "WIDGET_REPO_LISTED")

	// Atomic write semantics: commit + rollback on error.
	mustContain(t, body, "self._db.commit()")
	mustContain(t, body, "self._db.rollback()")
}

func TestPersistencePostgresRepoConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindPersistence, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "src/synthorg/persistence/postgres/widget_repo.py")

	// Postgres impl: psycopg + AsyncConnectionPool.
	mustContain(t, body, "import psycopg")
	mustContain(t, body, `if TYPE_CHECKING:`)
	mustContain(t, body, "from psycopg_pool import AsyncConnectionPool")

	// Secret-log redaction.
	mustContain(t, body, "error_type=type(exc).__name__")
	mustContain(t, body, "error=safe_error_description(exc)")
	if strings.Contains(body, "error=str(exc)") {
		t.Error("postgres repo must not call logger with error=str(exc)")
	}

	// Same event constants as SQLite -- protocol parity.
	mustContain(t, body, "WIDGET_REPO_FAILED")
}

func TestPersistenceConformanceTestConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("widget")
	files, err := Render(KindPersistence, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "tests/conformance/persistence/test_widget_repository.py")

	// Both backends exercised via the same dispatcher.
	mustContain(t, body, "SQLiteWidgetRepository")
	mustContain(t, body, "PostgresWidgetRepository")
	mustContain(t, body, `name == "sqlite":`)
	mustContain(t, body, `name == "postgres":`)
	mustContain(t, body, "pytestmark = pytest.mark.integration")
}
