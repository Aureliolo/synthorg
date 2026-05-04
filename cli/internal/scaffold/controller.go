package scaffold

import "fmt"

// renderController is implemented in a follow-up commit on this PR;
// dispatch is wired now so the Cobra subcommand graph is complete.
//
// The intended layout is:
//
//   - src/synthorg/api/controllers/<domain>.py (Litestar Controller with service injection)
//   - src/synthorg/api/services/<domain>_service.py
//   - tests/unit/api/controllers/test_<domain>.py
//   - WIRING.md (registration in api/auto_wire.py / api/app.py)
func renderController(_ Params) ([]RenderedFile, error) {
	return nil, fmt.Errorf("scaffold kind %q is not yet implemented", KindController)
}
