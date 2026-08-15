package cmd

import (
	"bytes"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// TestClassifyImageRemoval pins which block a `docker rmi` failure is.
// Docker opens every one of them with "conflict", and they do not share a
// remedy, so the distinction is the whole point: "in use" sends an
// operator looking for a container, and for a multiply-referenced image
// there is no container to find.
func TestClassifyImageRemoval(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		errMsg string
		want   imageRemovalBlock
	}{
		{
			"held by a running container",
			"Error response from daemon: conflict: unable to delete deadbeef (cannot be forced) - image is being used by running container abc123",
			rmiHeldByContainer,
		},
		{
			"held by a stopped container",
			"Error response from daemon: conflict: unable to delete deadbeef (must be forced) - image is being used by stopped container abc123",
			rmiHeldByContainer,
		},
		{
			"carries a second reference",
			"Error response from daemon: conflict: unable to delete deadbeef (must be forced) - image is referenced in multiple repositories",
			rmiMultipleReferences,
		},
		{
			"another image builds on it",
			"Error response from daemon: conflict: unable to delete deadbeef (cannot be forced) - image has dependent child images",
			rmiDependentChildren,
		},
		{
			"an unrecognised conflict is still benign",
			"Error: conflict: unable to delete deadbeef (must be forced)",
			rmiBlockedOther,
		},

		{"permission denied is a hard failure", "permission denied while trying to connect to the Docker daemon", rmiNotBlocked},
		{"missing image is a hard failure", "Error: no such image: deadbeef", rmiNotBlocked},
		{"network error is a hard failure", "network timeout contacting registry", rmiNotBlocked},
		{"empty message", "", rmiNotBlocked},

		// Case-sensitivity boundary: strings.Contains is case-sensitive
		// and Docker emits lowercase "conflict", so a capitalised variant
		// must NOT match.
		{"capitalised conflict does not match", "Conflict: unable to delete", rmiNotBlocked},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := classifyImageRemoval(errors.New(tt.errMsg)); got != tt.want {
				t.Errorf("classifyImageRemoval(%q) = %v, want %v", tt.errMsg, got, tt.want)
			}
		})
	}
}

// TestClassifyImageRemovalNilError guards the nil path: a successful
// removal must not be reported as a block.
func TestClassifyImageRemovalNilError(t *testing.T) {
	t.Parallel()
	if got := classifyImageRemoval(nil); got != rmiNotBlocked {
		t.Errorf("classifyImageRemoval(nil) = %v, want rmiNotBlocked", got)
	}
}

// TestClassifyImageRemovalWrappedError confirms the classifier reads
// through wrapped errors: it calls err.Error(), which flattens the chain.
func TestClassifyImageRemovalWrappedError(t *testing.T) {
	t.Parallel()
	wrapped := fmt.Errorf("rmi failed for deadbeef: %w", errors.New("conflict: image is being used by running container abc"))
	if got := classifyImageRemoval(wrapped); got != rmiHeldByContainer {
		t.Errorf("classifyImageRemoval(%q) = %v, want rmiHeldByContainer", wrapped, got)
	}
}

// TestBlockReasonNamesTheRemedy checks each block reports a distinct
// reason, and that a multiply-referenced image names the references
// standing in the way rather than blaming a container.
func TestBlockReasonNamesTheRemedy(t *testing.T) {
	t.Parallel()

	if got := blockReason(rmiHeldByContainer, nil); !strings.Contains(got, "container") {
		t.Errorf("blockReason(rmiHeldByContainer) = %q, want it to name a container", got)
	}

	refs := []string{"synthorg-sandbox:local", "ghcr.io/aureliolo/synthorg-sandbox@sha256:abc"}
	got := blockReason(rmiMultipleReferences, refs)
	if strings.Contains(got, "container") {
		t.Errorf("blockReason(rmiMultipleReferences) = %q, must not blame a container", got)
	}
	for _, ref := range refs {
		if !strings.Contains(got, ref) {
			t.Errorf("blockReason(rmiMultipleReferences) = %q, want it to name %q", got, ref)
		}
	}
}

// TestEmitCleanupSummary pins the post-cleanup output shape across every
// branch: the freed-vs-removed success line, the skipped-image hint, and
// the --keep guidance (which only renders in hints=always mode and only
// when at least one image was removed). It writes to a buffer-bound UI
// and asserts on substrings, matching the update_walk_test.go pattern.
func TestEmitCleanupSummary(t *testing.T) {
	t.Parallel()

	mkImages := func(n int) []oldImage {
		imgs := make([]oldImage, n)
		for i := range imgs {
			imgs[i] = oldImage{id: fmt.Sprintf("img%09d", i), display: "repo", sizeB: 1e6}
		}
		return imgs
	}

	tests := []struct {
		name        string
		old         []oldImage
		removed     int
		freedB      float64
		wantContain []string
		wantAbsent  []string
	}{
		{
			name:        "all removed with size freed",
			old:         mkImages(3),
			removed:     3,
			freedB:      1.5e9,
			wantContain: []string{"Freed", "1.5 GB", "3 image(s) removed", "--keep"},
			wantAbsent:  []string{"skipped"},
		},
		{
			name:        "removed with zero bytes freed",
			old:         mkImages(2),
			removed:     2,
			freedB:      0,
			wantContain: []string{"Removed 2 image(s)", "--keep"},
			wantAbsent:  []string{"Freed", "skipped"},
		},
		{
			name:        "partial removal reports skipped",
			old:         mkImages(3),
			removed:     1,
			freedB:      5e8,
			wantContain: []string{"Freed", "500.0 MB", "2 image(s) skipped", "--keep"},
		},
		{
			name:        "none removed reports only skipped",
			old:         mkImages(2),
			removed:     0,
			freedB:      0,
			wantContain: []string{"2 image(s) skipped"},
			wantAbsent:  []string{"Freed", "Removed", "--keep"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			var buf bytes.Buffer
			// Hints: "auto" (the default mode) so the test asserts the
			// --keep tip is visible to ordinary users: it is a HintNextStep,
			// which renders in auto, not a HintGuidance, which auto would
			// suppress. NoColor strips ANSI so substring assertions are stable.
			out := ui.NewUIWithOptions(&buf, ui.Options{NoColor: true, Hints: "auto"})

			emitCleanupSummary(out, tt.old, tt.removed, tt.freedB)

			got := buf.String()
			for _, want := range tt.wantContain {
				if !strings.Contains(got, want) {
					t.Errorf("summary missing %q\n--- got ---\n%s", want, got)
				}
			}
			for _, absent := range tt.wantAbsent {
				if strings.Contains(got, absent) {
					t.Errorf("summary unexpectedly contains %q\n--- got ---\n%s", absent, got)
				}
			}
		})
	}
}
