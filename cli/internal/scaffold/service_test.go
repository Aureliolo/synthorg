package scaffold

import (
	"strings"
	"testing"
)

// TestRenderServiceShape locks the file set the service scaffold emits.
// A drift here is intentional: update the test to match the new layout
// AND update docs/reference/cli-scaffolder.md so the user-facing
// inventory stays in sync.
func TestRenderServiceShape(t *testing.T) {
	t.Parallel()
	p, err := NewParams("ping")
	if err != nil {
		t.Fatalf("NewParams: %v", err)
	}
	files, err := Render(KindService, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}

	wantPaths := []string{
		"src/synthorg/ping/__init__.py",
		"src/synthorg/ping/service.py",
		"src/synthorg/ping/errors.py",
		"src/synthorg/observability/events/ping.py",
		"tests/unit/ping/__init__.py",
		"tests/unit/ping/test_service.py",
		"src/synthorg/ping/WIRING.md",
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

// TestServiceTemplateConventions asserts the rendered service.py
// hits each of the conventions the scaffold is supposed to encode.
// Every assertion maps to a rule from CLAUDE.md or a project gate; if
// a future convention shifts, fix the template AND update this test
// in the same PR -- never delete the assertion.
func TestServiceTemplateConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("ping")
	files, err := Render(KindService, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "src/synthorg/ping/service.py")

	// CLAUDE.md Logging: get_logger import + module-level `logger`.
	mustContain(t, body, "from synthorg.observability import get_logger")
	mustContain(t, body, "logger = get_logger(__name__)")

	// CLAUDE.md Logging: event constants from events.<domain>, never
	// string literals.
	mustContain(t, body, "from synthorg.observability.events.ping import")
	mustContain(t, body, "PING_SERVICE_STARTED")
	mustContain(t, body, "PING_SERVICE_STOPPED")

	// docs/reference/lifecycle-sync.md: dedicated _lifecycle_lock.
	mustContain(t, body, "self._lifecycle_lock = asyncio.Lock()")

	// CLAUDE.md Clock seam: `clock: Clock | None = None` + SystemClock default.
	mustContain(t, body, "clock: Clock | None = None")
	mustContain(t, body, "SystemClock()")

	// PEP 758 Python 3.14 + no __future__: forbidden imports/tokens
	// must NOT appear (regression fence).
	if strings.Contains(body, "from __future__") {
		t.Error("service.py must not contain `from __future__ import annotations` (Python 3.14 has PEP 649)")
	}

	// CLAUDE.md Comments: no reviewer / migration / round-narrative
	// citations should appear in scaffolded code.
	for _, banned := range []string{"CodeRabbit", "Round-", "ported from", "renamed from"} {
		if strings.Contains(body, banned) {
			t.Errorf("service.py contains banned phrase %q", banned)
		}
	}
}

// TestServiceErrorsConventions asserts the errors module follows the
// DomainError hierarchy without inventing new ErrorCode values.
func TestServiceErrorsConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("ping")
	files, err := Render(KindService, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "src/synthorg/ping/errors.py")

	mustContain(t, body, "from synthorg.core.domain_errors import DomainError, NotFoundError")
	mustContain(t, body, "class PingError(DomainError):")
	mustContain(t, body, "class PingNotFoundError(NotFoundError):")
	if strings.Contains(body, "raise Exception") || strings.Contains(body, "raise RuntimeError") {
		t.Error("errors.py must not raise bare Exception / RuntimeError")
	}
}

// TestServiceTestTemplateConventions asserts the generated test file
// passes the project's test conventions: pytest.mark.unit, FakeClock
// from the shared module, asyncio.TaskGroup for concurrent tasks.
func TestServiceTestTemplateConventions(t *testing.T) {
	t.Parallel()
	p, _ := NewParams("ping")
	files, err := Render(KindService, p)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	body := mustFile(t, files, "tests/unit/ping/test_service.py")

	mustContain(t, body, "pytestmark = pytest.mark.unit")
	mustContain(t, body, "from tests._shared.fake_clock import FakeClock")
	mustContain(t, body, "asyncio.TaskGroup")

	// check_mock_spec.py: any Mock/AsyncMock/MagicMock must declare
	// spec=. The current service test fixture has no mocks, but if a
	// future template adds one without spec=, this assertion catches
	// it. Walk every occurrence (not just the first) and find the
	// matching close paren by counting paren depth so nested calls
	// like ``Mock(spec=Sub())`` are handled correctly.
	for _, mockKind := range []string{"AsyncMock(", "MagicMock(", "Mock("} {
		start := 0
		for {
			rel := strings.Index(body[start:], mockKind)
			if rel == -1 {
				break
			}
			idx := start + rel
			depth := 0
			closeAbs := -1
			for i := idx + len(mockKind) - 1; i < len(body); i++ {
				switch body[i] {
				case '(':
					depth++
				case ')':
					depth--
					if depth == 0 {
						closeAbs = i
					}
				}
				if closeAbs != -1 {
					break
				}
			}
			if closeAbs == -1 {
				t.Fatalf("malformed %s call near offset %d", mockKind, idx)
			}
			call := body[idx : closeAbs+1]
			if !strings.Contains(call, "spec=") {
				t.Errorf("%s missing spec= argument: %s", mockKind, call)
			}
			start = closeAbs + 1
		}
	}
}

func mustFile(t *testing.T, files []RenderedFile, path string) string {
	t.Helper()
	for _, f := range files {
		if f.Path == path {
			return string(f.Contents)
		}
	}
	t.Fatalf("rendered files do not contain %q", path)
	return ""
}

func mustContain(t *testing.T, body, substr string) {
	t.Helper()
	if !strings.Contains(body, substr) {
		t.Errorf("rendered body missing %q", substr)
	}
}
