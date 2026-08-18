package cmd

import "testing"

// A re-exec'd child that dies to a signal reports no exit status at all, and
// os/exec surfaces that as -1. The parent hands whatever it gets to os.Exit,
// which takes the low byte, so -1 would reach the shell as 255: a code the
// CLI never documents and a caller cannot distinguish from a real failure
// mode. Every non-negative status is the child's own and stays untouched.
func TestNormalizeChildExitCode(t *testing.T) {
	tests := []struct {
		name string
		code int
		want int
	}{
		{"signal_terminated_becomes_runtime", -1, ExitRuntime},
		{"success_passes_through", ExitSuccess, ExitSuccess},
		{"runtime_passes_through", ExitRuntime, ExitRuntime},
		{"usage_passes_through", ExitUsage, ExitUsage},
		{"unhealthy_passes_through", ExitUnhealthy, ExitUnhealthy},
		{"update_available_passes_through", ExitUpdateAvail, ExitUpdateAvail},
		// A shell reports a signal death as 128+signum; a child that chose
		// to exit with that number is still reporting a status, so it is
		// the child's to keep.
		{"sigint_shell_convention_passes_through", 130, 130},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := normalizeChildExitCode(tt.code); got != tt.want {
				t.Errorf("normalizeChildExitCode(%d) = %d, want %d", tt.code, got, tt.want)
			}
		})
	}
}

// The mapped code is what the entrypoint reads back out, so the guard is
// only worth anything if it survives the ChildExitError round trip.
func TestNormalizeChildExitCode_survivesChildExitError(t *testing.T) {
	err := &ChildExitError{Code: normalizeChildExitCode(-1)}
	got, ok := ChildExitCode(err)
	if !ok {
		t.Fatalf("ChildExitCode did not recognise %T", err)
	}
	if got != ExitRuntime {
		t.Errorf("ChildExitCode = %d, want ExitRuntime (%d)", got, ExitRuntime)
	}
}
