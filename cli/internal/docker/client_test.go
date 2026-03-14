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
	info := Info{ComposeCmd: []string{"nonexistent-compose-12345"}, ComposePath: "nonexistent-compose-12345"}
	_, err := ComposeExecOutput(context.Background(), info, ".", "ps")
	if err == nil {
		t.Fatal("expected error for nonexistent compose")
	}
}

func TestComposeExecFailure(t *testing.T) {
	info := Info{ComposeCmd: []string{"nonexistent-compose-12345"}, ComposePath: "nonexistent-compose-12345"}
	err := ComposeExec(context.Background(), info, ".", "ps")
	if err == nil {
		t.Fatal("expected error for nonexistent compose")
	}
}

func TestComposeExecOutputParsesCommand(t *testing.T) {
	// "docker compose" equivalent using ComposeCmd slice.
	info := Info{ComposeCmd: []string{"go", "version"}, ComposePath: "go version"}
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
		ComposeCmd:     []string{"docker", "compose"},
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

func TestCheckMinVersions(t *testing.T) {
	tests := []struct {
		name           string
		dockerVersion  string
		composeVersion string
		wantWarnings   int
	}{
		{"both ok", "27.5.1", "2.32.1", 0},
		{"docker too old", "19.3.0", "2.32.1", 1},
		{"compose too old", "27.5.1", "1.29.0", 1},
		{"both too old", "19.3.0", "1.29.0", 2},
		{"exact minimum", "20.10.0", "2.0.0", 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			info := Info{
				DockerVersion:  tt.dockerVersion,
				ComposeVersion: tt.composeVersion,
			}
			warnings := CheckMinVersions(info)
			if len(warnings) != tt.wantWarnings {
				t.Errorf("got %d warnings, want %d: %v", len(warnings), tt.wantWarnings, warnings)
			}
		})
	}
}

func TestVersionAtLeast(t *testing.T) {
	tests := []struct {
		got  string
		min  string
		want bool
	}{
		{"27.5.1", "20.10.0", true},
		{"20.10.0", "20.10.0", true},
		{"20.10.1", "20.10.0", true},
		{"20.9.0", "20.10.0", false},
		{"19.3.0", "20.10.0", false},
		{"v2.32.1", "2.0.0", true},
		{"1.29.0", "2.0.0", false},
	}
	for _, tt := range tests {
		t.Run(tt.got+">="+tt.min, func(t *testing.T) {
			if got := versionAtLeast(tt.got, tt.min); got != tt.want {
				t.Errorf("versionAtLeast(%q, %q) = %v, want %v", tt.got, tt.min, got, tt.want)
			}
		})
	}
}
