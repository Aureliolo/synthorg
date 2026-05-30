package cmd

import (
	"slices"
	"testing"
)

// TestBuildDownArgs locks down the `docker compose down` argument
// construction. This is the data-destruction path: the --volumes flag
// permanently removes the named volumes (database, memory), so the
// table pins exactly when it is emitted, how --timeout is parsed, and
// the precise argument ordering. buildDownArgs reads the stopTimeout /
// stopVolumes package globals, so the cases mutate them in place and a
// single t.Cleanup restores the originals; the test must NOT run in
// parallel (shared mutable globals).
func TestBuildDownArgs(t *testing.T) {
	origTimeout, origVolumes := stopTimeout, stopVolumes
	t.Cleanup(func() { stopTimeout, stopVolumes = origTimeout, origVolumes })

	tests := []struct {
		name     string
		timeout  string
		volumes  bool
		wantArgs []string
		wantErr  bool
	}{
		{
			name:     "no flags",
			timeout:  "",
			volumes:  false,
			wantArgs: []string{"down"},
		},
		{
			name:     "volumes only",
			timeout:  "",
			volumes:  true,
			wantArgs: []string{"down", "--volumes"},
		},
		{
			name:     "timeout 30s",
			timeout:  "30s",
			volumes:  false,
			wantArgs: []string{"down", "--timeout", "30"},
		},
		{
			name:     "timeout 1m converts to 60",
			timeout:  "1m",
			volumes:  false,
			wantArgs: []string{"down", "--timeout", "60"},
		},
		{
			name:     "timeout 2h converts to 7200",
			timeout:  "2h",
			volumes:  false,
			wantArgs: []string{"down", "--timeout", "7200"},
		},
		{
			name:     "zero timeout is valid boundary",
			timeout:  "0s",
			volumes:  false,
			wantArgs: []string{"down", "--timeout", "0"},
		},
		{
			name:     "timeout and volumes preserves order",
			timeout:  "30s",
			volumes:  true,
			wantArgs: []string{"down", "--timeout", "30", "--volumes"},
		},
		{
			name:    "unparseable timeout",
			timeout: "notaduration",
			volumes: false,
			wantErr: true,
		},
		{
			name:    "negative timeout rejected",
			timeout: "-30s",
			volumes: false,
			wantErr: true,
		},
		{
			name:    "sub-second timeout rejected",
			timeout: "500ms",
			volumes: false,
			wantErr: true,
		},
		{
			name:    "fractional second timeout rejected",
			timeout: "1500ms",
			volumes: false,
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Assign both globals every iteration so no case inherits a
			// previous case's leftover state.
			stopTimeout, stopVolumes = tt.timeout, tt.volumes

			got, err := buildDownArgs()
			if tt.wantErr {
				if err == nil {
					t.Fatalf("buildDownArgs() = %v, want error", got)
				}
				if got != nil {
					t.Errorf("buildDownArgs() returned args %v alongside error, want nil", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("buildDownArgs() unexpected error: %v", err)
			}
			if !slices.Equal(got, tt.wantArgs) {
				t.Errorf("buildDownArgs() = %v, want %v", got, tt.wantArgs)
			}
			// Explicit data-destruction guard: --volumes appears in the
			// args if and only if the stopVolumes flag is set.
			hasVolumes := slices.Contains(got, "--volumes")
			if hasVolumes != tt.volumes {
				t.Errorf("--volumes present = %v, want %v (args=%v)", hasVolumes, tt.volumes, got)
			}
		})
	}
}
