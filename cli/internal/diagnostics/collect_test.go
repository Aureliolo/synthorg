package diagnostics

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

func TestTruncate(t *testing.T) {
	tests := []struct {
		name  string
		input string
		max   int
		want  string
	}{
		{"short", "hello", 10, "hello"},
		{"exact", "hello", 5, "hello"},
		{"truncated", "hello world", 5, "hello\n... (truncated)"},
		{"empty", "", 5, ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := truncate(tt.input, tt.max)
			if got != tt.want {
				t.Errorf("truncate(%q, %d) = %q, want %q", tt.input, tt.max, got, tt.want)
			}
		})
	}
}

func TestReportFormatText(t *testing.T) {
	r := Report{
		Timestamp:  "2026-03-14T00:00:00Z",
		OS:         "linux",
		Arch:       "amd64",
		CLIVersion: "dev",
		CLICommit:  "none",
	}
	text := r.FormatText()
	if text == "" {
		t.Fatal("FormatText returned empty string")
	}

	// Check key sections are present.
	for _, section := range []string{"Diagnostic Report", "Timestamp:", "OS:", "CLI:", "Health", "Containers", "Config"} {
		if !strings.Contains(text, section) {
			t.Errorf("FormatText missing section %q", section)
		}
	}
}

func TestReportFormatTextWithErrors(t *testing.T) {
	r := Report{
		Timestamp:  "2026-03-14T00:00:00Z",
		OS:         "linux",
		Arch:       "amd64",
		CLIVersion: "dev",
		CLICommit:  "none",
		Errors:     []string{"docker not found", "health unreachable"},
	}
	text := r.FormatText()
	if !strings.Contains(text, "Errors") {
		t.Error("FormatText should include Errors section")
	}
	if !strings.Contains(text, "docker not found") {
		t.Error("FormatText should include error details")
	}
}

func TestCollectDoesNotPanic(t *testing.T) {
	// Collect should never panic even with a bad state.
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	state := config.State{
		DataDir:     t.TempDir(),
		BackendPort: 99999, // unreachable port
	}
	report := Collect(ctx, state)

	if report.OS == "" {
		t.Error("OS should be set")
	}
	if report.CLIVersion == "" {
		t.Error("CLIVersion should be set")
	}
	if report.Timestamp == "" {
		t.Error("Timestamp should be set")
	}
}

func TestDiskInfo(t *testing.T) {
	info := diskInfo(context.Background(), t.TempDir())
	// Should return something (even "unavailable: ...")
	if info == "" {
		t.Error("diskInfo returned empty")
	}
}
