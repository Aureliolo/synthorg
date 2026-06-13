package diagnostics

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/images"
)

func TestTruncate(t *testing.T) {
	t.Parallel()

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
			t.Parallel()
			got := truncate(tt.input, tt.max)
			if got != tt.want {
				t.Errorf("truncate(%q, %d) = %q, want %q", tt.input, tt.max, got, tt.want)
			}
		})
	}
}

func TestReportFormatText(t *testing.T) {
	t.Parallel()

	r := Report{
		Timestamp:  "2026-03-14T00:00:00Z",
		OS:         "linux",
		Arch:       "amd64",
		CLIVersion: "dev",
		CLICommit:  "none",
		ContainerSummary: []ContainerDetail{
			{Name: "synthorg-backend-1", State: "running", Health: "healthy"},
		},
	}
	text := r.FormatText()
	if text == "" {
		t.Fatal("FormatText returned empty string")
	}

	// Check key sections are present.
	for _, section := range []string{"Diagnostic Report", "Timestamp:", "OS:", "CLI:", "Health", "Container Summary", "Config"} {
		if !strings.Contains(text, section) {
			t.Errorf("FormatText missing section %q", section)
		}
	}
}

func TestReportFormatTextWithErrors(t *testing.T) {
	t.Parallel()

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
	t.Parallel()

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
	t.Parallel()

	info := diskInfo(context.Background(), t.TempDir())
	// Should return something (even "unavailable: ...")
	if info == "" {
		t.Error("diskInfo returned empty")
	}
}

func TestParseContainerDetails(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name  string
		input string
		want  int
	}{
		{
			"single",
			`{"Name":"synthorg-backend-1","State":"running","Status":"Up 5 minutes","Health":"healthy"}`,
			1,
		},
		{
			"multiple_ndjson",
			"{\"Name\":\"backend\",\"State\":\"running\",\"Status\":\"Up\"}\n{\"Name\":\"web\",\"State\":\"exited\",\"Status\":\"Exited (1)\"}",
			2,
		},
		{
			"json_array",
			`[{"Name":"app","State":"running","Status":"Up"},{"Name":"db","State":"exited","Status":"Exited (1)"}]`,
			2,
		},
		{"empty", "", 0},
		{"invalid_json", "not json at all", 0},
		{"blank_lines", "\n\n\n", 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := parseContainerDetails(tt.input)
			if len(got) != tt.want {
				t.Errorf("parseContainerDetails: got %d details, want %d", len(got), tt.want)
			}
		})
	}
}

func TestParseContainerDetailsFields(t *testing.T) {
	t.Parallel()

	input := `{"Name":"synthorg-backend-1","State":"running","Status":"Up 5 minutes","Health":"healthy"}`
	details := parseContainerDetails(input)
	if len(details) != 1 {
		t.Fatalf("expected 1 detail, got %d", len(details))
	}
	d := details[0]
	if d.Name != "synthorg-backend-1" {
		t.Errorf("Name = %q, want %q", d.Name, "synthorg-backend-1")
	}
	if d.State != "running" {
		t.Errorf("State = %q, want %q", d.State, "running")
	}
	if d.Health != "healthy" {
		t.Errorf("Health = %q, want %q", d.Health, "healthy")
	}
}

func TestHasRunningContainers(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		details []ContainerDetail
		want    bool
	}{
		{"empty", nil, false},
		{"running", []ContainerDetail{{State: "running"}}, true},
		{"exited", []ContainerDetail{{State: "exited"}}, false},
		{"mixed", []ContainerDetail{{State: "exited"}, {State: "running"}}, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := hasRunningContainers(tt.details); got != tt.want {
				t.Errorf("hasRunningContainers = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestCheckPortsDetectsConflict(t *testing.T) {
	t.Parallel()

	ctx := context.Background()
	// Start a listener to occupy a port.
	var lc net.ListenConfig
	ln, err := lc.Listen(ctx, "tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = ln.Close() }()
	port := ln.Addr().(*net.TCPAddr).Port

	// Drain the accept queue so the dial in checkPorts reliably completes
	// its handshake. A real listening service accepts connections; an
	// unaccepted listener lets macOS drop/delay SYNs into the backlog under
	// load, timing out checkPorts' 1s dial and hiding the conflict (this is
	// the macOS-only flake on the -race CI runner). Accepting mirrors real
	// service behaviour and makes detection deterministic across platforms.
	go func() {
		for {
			conn, acceptErr := ln.Accept()
			if acceptErr != nil {
				return
			}
			_ = conn.Close()
		}
	}()

	conflicts := checkPorts(ctx, port, 0)
	if len(conflicts) == 0 {
		t.Error("expected port conflict for occupied port")
	}

	found := false
	for _, c := range conflicts {
		if strings.Contains(c, "backend") {
			found = true
		}
	}
	if !found {
		t.Error("expected conflict to mention 'backend'")
	}
}

func TestCheckPortsNoConflict(t *testing.T) {
	t.Parallel()

	// Port 0 is not a connectable port -- dialing 127.0.0.1:0 always fails.
	conflicts := checkPorts(context.Background(), 0, 0)
	if len(conflicts) != 0 {
		t.Errorf("expected no conflicts, got %v", conflicts)
	}
}

func TestFormatTextNewSections(t *testing.T) {
	t.Parallel()

	r := Report{
		Timestamp:         "2026-03-15T00:00:00Z",
		OS:                "linux",
		Arch:              "amd64",
		CLIVersion:        "dev",
		CLICommit:         "none",
		ComposeFileExists: true,
		ComposeFileValid:  ptrBool(true),
		PortConflicts:     []string{"port 3001 (backend) is already in use"},
		ImageStatus:       []string{"ghcr.io/aureliolo/synthorg-backend:latest: available"},
		ContainerSummary: []ContainerDetail{
			{Name: "backend", State: "running", Health: "healthy"},
		},
	}
	text := r.FormatText()
	for _, want := range []string{
		"Compose File",
		"Exists: yes  Valid: yes",
		"Container Summary",
		"backend: running (healthy)",
		"Port Conflicts",
		"port 3001",
		"Docker Images",
		"synthorg-backend",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("FormatText missing %q", want)
		}
	}
}

func TestParseComposeImageRefs(t *testing.T) {
	t.Parallel()

	compose := `services:
  backend:
    image: ghcr.io/aureliolo/synthorg-backend@sha256:abc123
    ports:
      - "3001:3001"
  web:
    image: ghcr.io/aureliolo/synthorg-web@sha256:def456
  sandbox:
    image: ghcr.io/aureliolo/synthorg-sandbox@sha256:ghi789
`
	tmp := filepath.Join(t.TempDir(), "compose.yml")
	if err := os.WriteFile(tmp, []byte(compose), 0o644); err != nil {
		t.Fatal(err)
	}

	refs := parseComposeImageRefs(tmp)
	if len(refs) != 3 {
		t.Fatalf("expected 3 refs, got %d: %v", len(refs), refs)
	}
	if refs["backend"] != "ghcr.io/aureliolo/synthorg-backend@sha256:abc123" {
		t.Errorf("backend = %q", refs["backend"])
	}
	if refs["web"] != "ghcr.io/aureliolo/synthorg-web@sha256:def456" {
		t.Errorf("web = %q", refs["web"])
	}
	if refs["sandbox"] != "ghcr.io/aureliolo/synthorg-sandbox@sha256:ghi789" {
		t.Errorf("sandbox = %q", refs["sandbox"])
	}
}

func TestParseComposeImageRefs_TagFormat(t *testing.T) {
	t.Parallel()

	compose := `services:
  backend:
    image: ghcr.io/aureliolo/synthorg-backend:0.3.9
`
	tmp := filepath.Join(t.TempDir(), "compose.yml")
	if err := os.WriteFile(tmp, []byte(compose), 0o644); err != nil {
		t.Fatal(err)
	}

	refs := parseComposeImageRefs(tmp)
	if refs["backend"] != "ghcr.io/aureliolo/synthorg-backend:0.3.9" {
		t.Errorf("backend = %q", refs["backend"])
	}
}

func TestParseComposeImageRefs_EmptyPath(t *testing.T) {
	t.Parallel()

	refs := parseComposeImageRefs("")
	if len(refs) != 0 {
		t.Errorf("expected empty map, got %v", refs)
	}
}

func TestParseComposeImageRefs_MissingFile(t *testing.T) {
	t.Parallel()

	refs := parseComposeImageRefs("/nonexistent/compose.yml")
	if len(refs) != 0 {
		t.Errorf("expected empty map, got %v", refs)
	}
}

func TestParseComposeImageRefs_PathTraversal(t *testing.T) {
	t.Parallel()

	refs := parseComposeImageRefs("../../etc/passwd")
	if len(refs) != 0 {
		t.Errorf("expected empty map for path traversal, got %v", refs)
	}
}

func TestParseComposeImageRefs_InvalidYAML(t *testing.T) {
	t.Parallel()

	tmp := filepath.Join(t.TempDir(), "compose.yml")
	if err := os.WriteFile(tmp, []byte("not: [valid: yaml: {{{"), 0o644); err != nil {
		t.Fatal(err)
	}
	refs := parseComposeImageRefs(tmp)
	if len(refs) != 0 {
		t.Errorf("expected empty map for invalid YAML, got %v", refs)
	}
}

func TestParseComposeImageRefs_NonSynthorgImages(t *testing.T) {
	t.Parallel()

	compose := `services:
  redis:
    image: redis:7-alpine
  backend:
    image: ghcr.io/aureliolo/synthorg-backend@sha256:abc123
`
	tmp := filepath.Join(t.TempDir(), "compose.yml")
	if err := os.WriteFile(tmp, []byte(compose), 0o644); err != nil {
		t.Fatal(err)
	}
	refs := parseComposeImageRefs(tmp)
	if len(refs) != 1 {
		t.Fatalf("expected 1 ref (backend only), got %d: %v", len(refs), refs)
	}
	if _, ok := refs["redis"]; ok {
		t.Error("should not include non-synthorg images")
	}
}

// TestDiagnosticImageRefPriority verifies the compose-ref-first priority
// used by checkImages: compose ref > images.RefForService (digest > tag).
func TestDiagnosticImageRefPriority(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name            string
		svc             string
		imageTag        string
		composeRefs     map[string]string
		verifiedDigests map[string]string
		want            string
	}{
		{
			name:        "compose ref takes priority over digest",
			svc:         "backend",
			imageTag:    "0.4.1",
			composeRefs: map[string]string{"backend": "ghcr.io/aureliolo/synthorg-backend@sha256:fromcompose"},
			verifiedDigests: map[string]string{
				"backend": "sha256:fromstate",
			},
			want: "ghcr.io/aureliolo/synthorg-backend@sha256:fromcompose",
		},
		{
			name:        "digest fallback when no compose ref",
			svc:         "backend",
			imageTag:    "0.4.1",
			composeRefs: map[string]string{},
			verifiedDigests: map[string]string{
				"backend": "sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
			},
			want: "ghcr.io/aureliolo/synthorg-backend@sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
		},
		{
			name:            "tag fallback when no compose ref and no digest",
			svc:             "web",
			imageTag:        "0.4.1",
			composeRefs:     map[string]string{},
			verifiedDigests: map[string]string{},
			want:            "ghcr.io/aureliolo/synthorg-web:0.4.1",
		},
		{
			name:     "compose ref for different service does not match",
			svc:      "web",
			imageTag: "0.4.1",
			composeRefs: map[string]string{
				"backend": "ghcr.io/aureliolo/synthorg-backend@sha256:abc",
			},
			verifiedDigests: map[string]string{
				"web": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
			},
			want: "ghcr.io/aureliolo/synthorg-web@sha256:1111111111111111111111111111111111111111111111111111111111111111",
		},
		{
			name:            "tag fallback when nil maps",
			svc:             "sandbox",
			imageTag:        "latest",
			composeRefs:     nil,
			verifiedDigests: nil,
			want:            "ghcr.io/aureliolo/synthorg-sandbox:latest",
		},
		{
			name:        "tag fallback when digest value is empty",
			svc:         "backend",
			imageTag:    "0.4.1",
			composeRefs: map[string]string{},
			verifiedDigests: map[string]string{
				"backend": "",
			},
			want: "ghcr.io/aureliolo/synthorg-backend:0.4.1",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			// Replicate the inline priority from checkImages:
			// compose ref > images.RefForService (digest > tag).
			got := tt.composeRefs[tt.svc]
			if got == "" {
				got = images.RefForService(tt.svc, tt.imageTag, tt.verifiedDigests)
			}
			if got != tt.want {
				t.Errorf("diagnostic ref for %q = %q, want %q", tt.svc, got, tt.want)
			}
		})
	}
}

func ptrBool(v bool) *bool { return &v }
