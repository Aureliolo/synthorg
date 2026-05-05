package scaffold

import "fmt"

// renderPersistence emits the dual-backend repository skeleton:
//
//   - src/synthorg/<domain>/models.py                              -- frozen Pydantic entity model
//   - src/synthorg/persistence/<domain>_protocol.py                -- Repository protocol
//   - src/synthorg/persistence/sqlite/<domain>_repo.py             -- SQLite impl
//   - src/synthorg/persistence/postgres/<domain>_repo.py           -- Postgres impl
//   - src/synthorg/observability/events/<domain>_repo.py           -- repo event constants
//   - tests/conformance/persistence/test_<domain>_repository.py    -- parametrised dual-backend
//   - src/synthorg/persistence/<domain>_WIRING.md                  -- schema + atlas + backend exposure
//
// The Pydantic model carries a single `payload: str` placeholder so the
// repos compile and tests run end-to-end against a fresh schema; the
// WIRING.md walks the user through replacing the placeholder with the
// real entity shape and re-running `atlas migrate diff`.
//
// The repo event constants live at events/<domain>_repo.py while the
// service scaffold owns events/<domain>.py; the two are deliberately
// disjoint so a domain that needs BOTH layers can be scaffolded in
// either order without `Overwrite=false` colliding.
func renderPersistence(p Params) ([]RenderedFile, error) {
	files := []struct {
		out string
		tpl string
	}{
		{
			fmt.Sprintf("src/synthorg/%s/models.py", p.Domain),
			"persistence_models.py.tmpl",
		},
		{
			fmt.Sprintf("src/synthorg/persistence/%s_protocol.py", p.Domain),
			"persistence_protocol.py.tmpl",
		},
		{
			fmt.Sprintf("src/synthorg/persistence/sqlite/%s_repo.py", p.Domain),
			"persistence_sqlite_repo.py.tmpl",
		},
		{
			fmt.Sprintf("src/synthorg/persistence/postgres/%s_repo.py", p.Domain),
			"persistence_postgres_repo.py.tmpl",
		},
		{
			fmt.Sprintf("src/synthorg/observability/events/%s_repo.py", p.Domain),
			"persistence_events.py.tmpl",
		},
		{
			fmt.Sprintf("tests/conformance/persistence/test_%s_repository.py", p.Domain),
			"persistence_conformance_test.py.tmpl",
		},
		{
			fmt.Sprintf("src/synthorg/persistence/%s_WIRING.md", p.Domain),
			"persistence_wiring.md.tmpl",
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
