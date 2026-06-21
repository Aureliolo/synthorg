package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// msgNothingToStop is the human-readable line shown by the teardown paths
// (`wipe` / `uninstall`) when there is nothing to stop: no compose.yml on
// disk, or `docker compose down` reporting that no configuration file was
// provided. It replaces the raw docker "no configuration file provided"
// jargon with something an operator can act on.
const msgNothingToStop = "Nothing to stop, SynthOrg is not initialised."

// dockerNoComposeMarker is the substring docker emits when `compose down`
// runs with no compose file present. Matched case-insensitively against the
// sanitised error text so the teardown paths can translate it rather than
// leaking it.
const dockerNoComposeMarker = "no configuration file provided"

// isNotInitialisedErr reports whether err is the docker "no configuration
// file provided" failure, i.e. `compose down` invoked with no compose file.
// Teardown treats this as "already stopped", never a hard failure.
func isNotInitialisedErr(err error) bool {
	if err == nil {
		return false
	}
	return strings.Contains(strings.ToLower(err.Error()), dockerNoComposeMarker)
}

// composeFilePath returns the path to compose.yml under safeDir when it
// exists. It returns ("", nil) when the file is absent (teardown treats that
// as "nothing to stop") and ("", err) for any other stat failure (e.g. a
// permission error) so the caller can warn rather than silently skip the
// container teardown while a stack may still be running.
//
// safeDir is the output of safeStateDir -> config.SecurePath (absolute +
// clean), so the os.Stat below operates on an already-sanitised path.
func composeFilePath(safeDir string) (string, error) {
	composePath := filepath.Join(safeDir, "compose.yml")
	// CodeQL go/path-injection on this sink is accepted by design: safeDir is
	// the operator's own --data-dir on a single-user CLI (no privilege
	// boundary), already format-validated by SecurePath. Containment is
	// impossible because the contract honours an arbitrary absolute
	// --data-dir verbatim.
	if _, err := os.Stat(composePath); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", nil
		}
		return "", fmt.Errorf("checking compose.yml in %s: %w", safeDir, err)
	}
	return composePath, nil
}

// detectDockerForTeardown detects Docker without ever failing the teardown.
// When Docker is unavailable it warns to errOut and reports false so the
// caller skips backup + container teardown but still removes the data dir.
func detectDockerForTeardown(ctx context.Context, errOut *ui.UI) (docker.Info, bool) {
	info, err := docker.Detect(ctx)
	if err != nil {
		errOut.Warn(fmt.Sprintf("Docker not available, cannot stop containers: %v", err))
		return docker.Info{}, false
	}
	return info, true
}
