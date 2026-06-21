package cmd

import (
	"context"
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
// exists, or "" when it does not. Teardown treats a missing compose file
// as "nothing to stop" rather than an error.
//
// safeDir is the output of safeStateDir -> config.SecurePath (absolute +
// clean), so the os.Stat below operates on an already-sanitised path.
func composeFilePath(safeDir string) string {
	composePath := filepath.Join(safeDir, "compose.yml")
	if _, err := os.Stat(composePath); err != nil {
		return ""
	}
	return composePath
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
