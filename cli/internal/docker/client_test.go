package docker

import (
	"context"
	"strings"
	"testing"
)

func TestInstallHint(t *testing.T) {
	tests := []struct {
		goos     string
		contains string
	}{
		{"darwin", "mac"},
		{"windows", "windows"},
		{"linux", "Engine"},
		{"freebsd", "Engine"},
	}
	for _, tt := range tests {
		t.Run(tt.goos, func(t *testing.T) {
			hint := InstallHint(tt.goos)
			if hint == "" {
				t.Error("hint is empty")
			}
			if !strings.Contains(strings.ToLower(hint), strings.ToLower(tt.contains)) {
				t.Errorf("hint for %s = %q, want to contain %q", tt.goos, hint, tt.contains)
			}
		})
	}
}

func TestDaemonHint(t *testing.T) {
	tests := []struct {
		goos     string
		contains string
	}{
		{"darwin", "Docker Desktop"},
		{"windows", "Docker Desktop"},
		{"linux", "systemctl"},
		{"freebsd", "systemctl"},
	}
	for _, tt := range tests {
		t.Run(tt.goos, func(t *testing.T) {
			hint := DaemonHint(tt.goos)
			if hint == "" {
				t.Error("hint is empty")
			}
			if !strings.Contains(hint, tt.contains) {
				t.Errorf("hint for %s = %q, want to contain %q", tt.goos, hint, tt.contains)
			}
		})
	}
}

func TestRunCmdSuccess(t *testing.T) {
	out, err := RunCmd(context.Background(), "go", "version")
	if err != nil {
		t.Fatalf("RunCmd(go version): %v", err)
	}
	if !strings.Contains(out, "go version") {
		t.Errorf("expected 'go version' in output, got %q", out)
	}
}

func TestRunCmdFailure(t *testing.T) {
	_, err := RunCmd(context.Background(), "nonexistent-command-12345")
	if err == nil {
		t.Fatal("expected error for nonexistent command")
	}
}

func TestRunCmdStderr(t *testing.T) {
	// Run a command that writes to stderr.
	_, err := RunCmd(context.Background(), "go", "build", "nonexistent-package-xyz")
	if err == nil {
		t.Fatal("expected error")
	}
	// Error should include stderr content.
	if err.Error() == "" {
		t.Error("error message should not be empty")
	}
}

func TestComposeExecOutputFailure(t *testing.T) {
	info := Info{ComposePath: "nonexistent-compose-12345"}
	_, err := ComposeExecOutput(context.Background(), info, ".", "ps")
	if err == nil {
		t.Fatal("expected error for nonexistent compose")
	}
}

func TestComposeExecFailure(t *testing.T) {
	info := Info{ComposePath: "nonexistent-compose-12345"}
	err := ComposeExec(context.Background(), info, ".", "ps")
	if err == nil {
		t.Fatal("expected error for nonexistent compose")
	}
}

func TestComposeExecOutputParsesCommand(t *testing.T) {
	// "docker compose" should be split into two parts.
	info := Info{ComposePath: "go version"}
	out, err := ComposeExecOutput(context.Background(), info, ".")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "go version") {
		t.Errorf("expected go version output, got %q", out)
	}
}

func TestInfoStruct(t *testing.T) {
	info := Info{
		DockerPath:     "/usr/bin/docker",
		DockerVersion:  "24.0.7",
		ComposePath:    "docker compose",
		ComposeVersion: "2.23.0",
		ComposeV2:      true,
	}
	if info.DockerPath == "" {
		t.Error("DockerPath should not be empty")
	}
	if !info.ComposeV2 {
		t.Error("ComposeV2 should be true")
	}
}
