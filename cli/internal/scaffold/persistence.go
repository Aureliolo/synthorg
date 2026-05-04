package scaffold

import "fmt"

// renderPersistence is implemented in a follow-up commit on this PR;
// dispatch is wired now so the Cobra subcommand graph is complete.
//
// The intended layout (matching cli/internal/scaffold/templates/) is:
//
//   - src/synthorg/persistence/<domain>_protocol.py (Repository protocol)
//   - src/synthorg/persistence/sqlite/<domain>_repo.py
//   - src/synthorg/persistence/postgres/<domain>_repo.py
//   - tests/conformance/persistence/test_<domain>_repository.py (parametrised dual-backend)
//   - WIRING.md (PersistenceBackend exposure + atlas migrate diff steps)
func renderPersistence(_ Params) ([]RenderedFile, error) {
	return nil, fmt.Errorf("scaffold kind %q is not yet implemented", KindPersistence)
}
