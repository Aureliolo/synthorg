package cmd

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// pullAndPersist verifies and pulls the target images, then persists the new
// config only after a successful pull. compose.yml is backed up first and
// rolled back on any failure so a half-applied update never leaves the stack
// claiming images it does not have.
func pullAndPersist(ctx context.Context, cmd *cobra.Command, info docker.Info, state config.State, tag, safeDir string, preserveCompose bool) (config.State, error) {
	opts := GetGlobalOpts(ctx)
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())

	// Back up existing compose.yml for rollback on failure.
	composePath := filepath.Join(safeDir, "compose.yml")
	rollback := composeRollback(cmd, composePath)

	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())

	// Verify + write compose atomically: compose.yml is only updated after
	// verification succeeds (or when --skip-verify explicitly skips it).
	digestPins, err := verifyAndPinForUpdate(ctx, info, state, tag, safeDir, preserveCompose, out, errOut)
	if err != nil {
		rollback()
		return state, err
	}

	// Use newly verified digest pins for the pull so standalone images
	// (sandbox, sidecar, fine-tune) resolve to pinned references. Merge
	// fresh pins on top of any existing ones (e.g. cached DHI keys when
	// the verify step hit the DHI cache) so the pull sees the union, not
	// just the freshly-verified subset.
	mergedPins := mergeVerifiedDigests(state.VerifiedDigests, digestPins)
	pullState := state
	pullState.ImageTag = tag
	pullState.VerifiedDigests = mergedPins
	if _, err := pullAllImages(ctx, cmd, info, safeDir, pullState, out); err != nil {
		rollback()
		return state, err
	}

	// Persist config only after successful pull so a failed pull
	// doesn't leave state claiming images are at the new version.
	// VerifiedImageTag tracks which tag the SynthOrg pins were verified
	// against; hasSynthOrgDigests rejects the cache when this drifts.
	updatedState := state
	updatedState.ImageTag = tag
	updatedState.VerifiedDigests = mergedPins
	updatedState.VerifiedImageTag = tag
	if err := config.Save(updatedState); err != nil {
		rollback()
		return state, fmt.Errorf("saving config: %w", err)
	}
	return updatedState, nil
}

// composeRollback snapshots the existing compose.yml and returns a closure that
// restores it (or removes a freshly-written one when no prior file existed) so a
// failed verify/pull never leaves a half-applied compose.yml behind.
func composeRollback(cmd *cobra.Command, composePath string) func() {
	backup, backupErr := os.ReadFile(composePath) //nolint:gosec // G304: composePath is <data-dir>/compose.yml under the SecurePath-cleaned data dir

	return func() {
		switch {
		case backupErr == nil:
			// A prior compose.yml was read successfully: restore it.
			if wErr := os.WriteFile(composePath, backup, 0o600); wErr != nil { //nolint:gosec // G304: composePath is <data-dir>/compose.yml under the SecurePath-cleaned data dir
				_, _ = fmt.Fprintf(cmd.ErrOrStderr(),
					"Warning: failed to restore compose.yml backup: %v\n", wErr)
			}
		case errors.Is(backupErr, os.ErrNotExist):
			// No prior compose.yml existed, so remove the one we wrote.
			if rErr := os.Remove(composePath); rErr != nil && !errors.Is(rErr, os.ErrNotExist) {
				_, _ = fmt.Fprintf(cmd.ErrOrStderr(),
					"Warning: failed to clean up compose.yml: %v\n", rErr)
			}
		default:
			// The pre-existing compose.yml could not be read (e.g. a permission
			// or transient I/O error). It may still exist, so do NOT remove it:
			// a destructive cleanup here could delete a file we merely failed to
			// back up. Leave the current file in place.
			_, _ = fmt.Fprintf(cmd.ErrOrStderr(),
				"Warning: could not read compose.yml for backup (%v); "+
					"leaving the current file in place on rollback\n", backupErr)
		}
	}
}

// mergeVerifiedDigests overlays fresh pins on top of existing ones, returning
// a new map so the caller can assign without aliasing the original. Returns
// nil only when both inputs are nil/empty (lets compose.ParamsFromState's
// nil-pin fallback path fire when there is nothing to write).
func mergeVerifiedDigests(existing, fresh map[string]string) map[string]string {
	if len(existing) == 0 && len(fresh) == 0 {
		return nil
	}
	out := make(map[string]string, len(existing)+len(fresh))
	maps.Copy(out, existing)
	maps.Copy(out, fresh)
	return out
}
