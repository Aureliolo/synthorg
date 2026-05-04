package scaffold

import "fmt"

// renderController emits a Litestar controller + service-layer skeleton:
//
//   - src/synthorg/api/controllers/<domain>.py             -- Controller with list / get / delete endpoints
//   - src/synthorg/api/services/<domain>_service.py        -- Service-layer facade over the repository
//   - tests/unit/api/controllers/test_<domain>.py          -- Service-factory + list-roundtrip coverage
//   - src/synthorg/api/controllers/<domain>_WIRING.md      -- registration in api/app.py + cursor pagination upgrade
//
// The controller depends on the persistence scaffold's repository
// being exposed on PersistenceBackend; the WIRING.md walks the user
// through the registration step and through promoting the placeholder
// limit/offset pagination to cursor pagination once the endpoint goes
// live.
func renderController(p Params) ([]RenderedFile, error) {
	files := []struct {
		out string
		tpl string
	}{
		{
			fmt.Sprintf("src/synthorg/api/controllers/%s.py", p.Domain),
			"controller_controller.py.tmpl",
		},
		{
			fmt.Sprintf("src/synthorg/api/services/%s_service.py", p.Domain),
			"controller_service.py.tmpl",
		},
		{
			fmt.Sprintf("tests/unit/api/controllers/test_%s.py", p.Domain),
			"controller_test.py.tmpl",
		},
		{
			fmt.Sprintf("src/synthorg/api/controllers/%s_WIRING.md", p.Domain),
			"controller_wiring.md.tmpl",
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
