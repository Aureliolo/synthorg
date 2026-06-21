package cmd

import (
	"bytes"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// TestIsImageInUse pins the classifier that decides whether a
// `docker rmi` failure is a benign "image still in use" skip (warn and
// continue) or a hard failure that must surface as a runtime error. The
// match is a case-sensitive substring test against the four phrases
// Docker emits for in-use / dependent images.
func TestIsImageInUse(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		errMsg string
		want   bool
	}{
		{"image being used", "Error response from daemon: image is being used by running container abc123", true},
		{"conflict", "Error: conflict: unable to delete deadbeef (must be forced)", true},
		{"dependent child images", "Error: image has dependent child images", true},
		{"image referenced", "Error: image is referenced in multiple repositories", true},

		{"permission denied is a hard failure", "permission denied while trying to connect to the Docker daemon", false},
		{"missing image is a hard failure", "Error: no such image: deadbeef", false},
		{"network error is a hard failure", "network timeout contacting registry", false},
		{"empty message", "", false},

		// Case-sensitivity boundary: strings.Contains is case-sensitive
		// and Docker emits lowercase "conflict", so a capitalised variant
		// must NOT match.
		{"capitalised conflict does not match", "Conflict: unable to delete", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := isImageInUse(errors.New(tt.errMsg)); got != tt.want {
				t.Errorf("isImageInUse(%q) = %v, want %v", tt.errMsg, got, tt.want)
			}
		})
	}
}

// TestIsImageInUseWrappedError confirms the classifier reads through
// wrapped errors: isImageInUse calls err.Error(), which flattens the
// wrapped chain, so an in-use sentinel wrapped with %w is still matched.
func TestIsImageInUseWrappedError(t *testing.T) {
	t.Parallel()
	wrapped := fmt.Errorf("rmi failed for deadbeef: %w", errors.New("conflict: in use"))
	if !isImageInUse(wrapped) {
		t.Errorf("isImageInUse(%q) = false, want true (wrapped in-use error)", wrapped)
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
