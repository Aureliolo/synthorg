package scaffold

import "fmt"

// renderService emits a six-file service skeleton:
//
//   - src/synthorg/<domain>/__init__.py     -- empty package marker (D104 carve-out)
//   - src/synthorg/<domain>/service.py      -- Service class with lifecycle lock + Clock seam + structured logging
//   - src/synthorg/<domain>/errors.py       -- DomainError-rooted exception family
//   - tests/unit/<domain>/__init__.py       -- empty package marker
//   - tests/unit/<domain>/test_service.py   -- FakeClock + Mock(spec=) integration smoke
//   - WIRING.md                             -- copy-paste snippets for EXCEPTION_HANDLERS / app boot
//
// Each file is conventions-clean on its own (passes ruff / mypy / every
// active scripts/check_*.py gate); the WIRING.md describes the manual
// step the user takes to plug the new service into application boot.
func renderService(p Params) ([]RenderedFile, error) {
	files := []struct {
		out string
		tpl string
	}{
		{fmt.Sprintf("src/synthorg/%s/__init__.py", p.Domain), "service_package_init.py.tmpl"},
		{fmt.Sprintf("src/synthorg/%s/service.py", p.Domain), "service_service.py.tmpl"},
		{fmt.Sprintf("src/synthorg/%s/errors.py", p.Domain), "service_errors.py.tmpl"},
		{fmt.Sprintf("src/synthorg/observability/events/%s.py", p.Domain), "service_events.py.tmpl"},
		{fmt.Sprintf("tests/unit/%s/__init__.py", p.Domain), "service_test_init.py.tmpl"},
		{fmt.Sprintf("tests/unit/%s/test_service.py", p.Domain), "service_test.py.tmpl"},
		{fmt.Sprintf("src/synthorg/%s/WIRING.md", p.Domain), "service_wiring.md.tmpl"},
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
