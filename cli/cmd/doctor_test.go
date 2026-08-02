package cmd

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/diagnostics"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

func TestClassifyDoctor(t *testing.T) {
	t.Parallel()

	boolPtr := func(v bool) *bool { return &v }

	tests := []struct {
		name         string
		report       diagnostics.Report
		wantStatus   doctorStatus
		wantCount    int      // expected number of issues
		wantContains []string // substrings that must each appear in at least one issue
	}{
		{
			name: "all healthy",
			report: diagnostics.Report{
				HealthStatus: "200",
				ContainerSummary: []diagnostics.ContainerDetail{
					{Name: "backend", Health: "healthy"},
					{Name: "web", Health: "healthy"},
				},
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus: doctorHealthy,
			wantCount:  0,
		},
		{
			name: "backend unreachable",
			report: diagnostics.Report{
				HealthStatus:      "unreachable",
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus:   doctorErrors,
			wantCount:    1,
			wantContains: []string{"backend unreachable"},
		},
		{
			name: "backend serving with an unresolved dependency",
			report: diagnostics.Report{
				HealthStatus:      "503",
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus:   doctorErrors,
			wantCount:    1,
			wantContains: []string{"dependency not ready", "HTTP 503"},
		},
		{
			name: "backend genuinely unhealthy",
			report: diagnostics.Report{
				HealthStatus:      "500",
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus:   doctorErrors,
			wantCount:    1,
			wantContains: []string{"backend unhealthy", "HTTP 500"},
		},
		{
			name: "container starting is warning",
			report: diagnostics.Report{
				HealthStatus: "200",
				ContainerSummary: []diagnostics.ContainerDetail{
					{Name: "backend", Health: "healthy"},
					{Name: "sandbox", State: "running", Health: "starting"},
				},
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus:   doctorWarnings,
			wantCount:    1,
			wantContains: []string{"sandbox still starting"},
		},
		{
			name: "container unhealthy is error",
			report: diagnostics.Report{
				HealthStatus: "200",
				ContainerSummary: []diagnostics.ContainerDetail{
					{Name: "backend", Health: "unhealthy"},
				},
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus:   doctorErrors,
			wantCount:    1,
			wantContains: []string{"backend unhealthy"},
		},
		{
			name: "container exited is error",
			report: diagnostics.Report{
				HealthStatus: "200",
				ContainerSummary: []diagnostics.ContainerDetail{
					{Name: "web", State: "exited"},
				},
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus:   doctorErrors,
			wantCount:    1,
			wantContains: []string{"web exited"},
		},
		{
			name: "compose missing",
			report: diagnostics.Report{
				HealthStatus:      "200",
				ComposeFileExists: false,
			},
			wantStatus:   doctorErrors,
			wantCount:    1,
			wantContains: []string{"compose.yml not found"},
		},
		{
			name: "compose invalid",
			report: diagnostics.Report{
				HealthStatus:      "200",
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(false),
			},
			wantStatus:   doctorErrors,
			wantCount:    1,
			wantContains: []string{"compose.yml is invalid"},
		},
		{
			name: "port conflicts",
			report: diagnostics.Report{
				HealthStatus:      "200",
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
				PortConflicts:     []string{"3001 in use by other-process"},
			},
			wantStatus:   doctorErrors,
			wantCount:    1,
			wantContains: []string{"port conflict"},
		},
		{
			name: "collection errors propagated",
			report: diagnostics.Report{
				HealthStatus:      "200",
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
				Errors:            []string{"docker not found", "compose not found"},
			},
			wantStatus:   doctorErrors,
			wantCount:    2,
			wantContains: []string{"docker not found", "compose not found"},
		},
		{
			name: "errors take precedence over warnings",
			report: diagnostics.Report{
				HealthStatus: "unreachable",
				ContainerSummary: []diagnostics.ContainerDetail{
					{Name: "sandbox", State: "running", Health: "starting"},
				},
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus:   doctorErrors,
			wantCount:    1, // only errors returned, warnings discarded
			wantContains: []string{"backend unreachable"},
		},
		{
			name: "empty health status is not checked",
			report: diagnostics.Report{
				HealthStatus: "",
				ContainerSummary: []diagnostics.ContainerDetail{
					{Name: "backend", Health: "healthy"},
				},
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus: doctorHealthy,
			wantCount:  0,
		},
		{
			name: "no containers with compose is warning",
			report: diagnostics.Report{
				HealthStatus:      "200",
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus:   doctorWarnings,
			wantCount:    1,
			wantContains: []string{"no containers detected"},
		},
		{
			name: "compose validity not checked is warning",
			report: diagnostics.Report{
				HealthStatus: "200",
				ContainerSummary: []diagnostics.ContainerDetail{
					{Name: "backend", Health: "healthy"},
				},
				ComposeFileExists: true,
				ComposeFileValid:  nil,
			},
			wantStatus:   doctorWarnings,
			wantCount:    1,
			wantContains: []string{"validity not checked"},
		},
		{
			name: "unavailable image is warning",
			report: diagnostics.Report{
				HealthStatus: "200",
				ContainerSummary: []diagnostics.ContainerDetail{
					{Name: "backend", Health: "healthy"},
				},
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
				ImageStatus:       []string{"ghcr.io/aureliolo/synthorg-backend:latest: not found"},
			},
			wantStatus:   doctorWarnings,
			wantCount:    1,
			wantContains: []string{"not found"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			gotStatus, gotIssues := classifyDoctor(tt.report)
			if gotStatus != tt.wantStatus {
				t.Errorf("classifyDoctor() status = %d, want %d", gotStatus, tt.wantStatus)
			}
			if len(gotIssues) != tt.wantCount {
				t.Errorf("classifyDoctor() issues count = %d, want %d: %v", len(gotIssues), tt.wantCount, gotIssues)
			}
			for _, want := range tt.wantContains {
				found := false
				for _, issue := range gotIssues {
					if strings.Contains(issue, want) {
						found = true
						break
					}
				}
				if !found {
					t.Errorf("classifyDoctor() issues %v missing expected substring %q", gotIssues, want)
				}
			}
		})
	}
}

// TestClassifyDoctorIssue pins the pattern table's order. It is
// first-match-wins over substrings, and the health messages all carry
// "unhealthy", so a containers row evaluated first would claim them:
// the operator would be offered a container restart that cannot fix a
// backend health failure, and `doctor --checks=health --fix` would drop
// the issue entirely because it was filed under the wrong category.
func TestClassifyDoctorIssue(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name         string
		issue        string
		wantKind     doctorIssueKind
		wantCategory string
	}{
		{
			name:         "dependency not ready is an unfixable health issue",
			issue:        "backend serving, dependency not ready (HTTP 503)",
			wantKind:     doctorIssueUnfixable,
			wantCategory: "health",
		},
		{
			name:         "backend unhealthy is an unfixable health issue",
			issue:        "backend unhealthy (HTTP 500)",
			wantKind:     doctorIssueUnfixable,
			wantCategory: "health",
		},
		{
			name:         "backend unreachable is an unfixable health issue",
			issue:        "backend unreachable",
			wantKind:     doctorIssueUnfixable,
			wantCategory: "health",
		},
		{
			// The container error is `<name> <status>`, so a container
			// literally named `backend` collides with the health message
			// on everything but the "(HTTP" suffix. This one IS restartable.
			name:         "an unhealthy backend container is a restart candidate",
			issue:        "backend unhealthy",
			wantKind:     doctorIssueRestart,
			wantCategory: "containers",
		},
		{
			name:         "an exited container is a restart candidate",
			issue:        "web exited",
			wantKind:     doctorIssueRestart,
			wantCategory: "containers",
		},
		{
			name:         "a starting container is not fixable by restarting",
			issue:        "sandbox still starting",
			wantKind:     doctorIssueUnfixable,
			wantCategory: "containers",
		},
		{
			name:         "a missing compose file is regenerable",
			issue:        "compose.yml not found",
			wantKind:     doctorIssueComposeFix,
			wantCategory: "compose",
		},
		{
			name:         "anything unmatched falls back to the catch-all",
			issue:        "something nobody anticipated",
			wantKind:     doctorIssueUnfixable,
			wantCategory: "errors",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got := classifyDoctorIssue(tc.issue)
			if got.kind != tc.wantKind {
				t.Errorf("classifyDoctorIssue(%q).kind = %v, want %v", tc.issue, got.kind, tc.wantKind)
			}
			if got.category != tc.wantCategory {
				t.Errorf("classifyDoctorIssue(%q).category = %q, want %q", tc.issue, got.category, tc.wantCategory)
			}
		})
	}
}

// TestDoctorHealthError covers every branch of the status-to-message
// mapping, including the default arm the 503 case used to shadow.
func TestDoctorHealthError(t *testing.T) {
	t.Parallel()

	cases := []struct {
		status   string
		wantIs   bool
		contains string
	}{
		{status: "200", wantIs: false},
		{status: "", wantIs: false},
		{status: "unreachable", wantIs: true, contains: "backend unreachable"},
		{status: "503", wantIs: true, contains: "dependency not ready"},
		{status: "500", wantIs: true, contains: "backend unhealthy"},
		{status: "404", wantIs: true, contains: "backend unhealthy"},
	}

	for _, tc := range cases {
		t.Run(tc.status, func(t *testing.T) {
			t.Parallel()
			got, isErr := doctorHealthError(tc.status)
			if isErr != tc.wantIs {
				t.Fatalf("doctorHealthError(%q) reported %v, want %v", tc.status, isErr, tc.wantIs)
			}
			if tc.contains != "" && !strings.Contains(got, tc.contains) {
				t.Errorf("doctorHealthError(%q) = %q, want it to contain %q", tc.status, got, tc.contains)
			}
		})
	}
}

// TestDoctorReportsConfigCoercions covers the diagnosis half of the
// config-recovery story: when the running binary does not recognise a
// persisted setting it substitutes a default, and doctor has to say so.
// Without this the operator sees a stack running on values they never
// chose, with the on-disk file still showing the originals.
func TestDoctorReportsConfigCoercions(t *testing.T) {
	// Rendered through Coercion.String() rather than restated, so a change
	// to the operator-facing wording cannot leave doctor asserting text no
	// command ever emits.
	coercion := config.Coercion{
		Field:    "channel",
		Rejected: "nightly",
		Applied:  "stable",
		Allowed:  "dev, stable",
	}.String()
	base := diagnostics.Report{
		HealthStatus:      "200",
		ComposeFileExists: true,
		ContainerSummary:  []diagnostics.ContainerDetail{{Name: "backend", Health: "healthy"}},
		ConfigCoercions:   []string{coercion},
	}

	t.Run("surfaced as a warning by default", func(t *testing.T) {
		defer func(saved string) { doctorChecks = saved }(doctorChecks)
		doctorChecks = ""

		status, issues := classifyDoctor(base)
		if status != doctorWarnings {
			t.Errorf("status = %v, want %v", status, doctorWarnings)
		}
		var matched int
		for _, issue := range issues {
			if issue == "config "+coercion {
				matched++
			}
		}
		if matched != 1 {
			t.Errorf("issues = %v, want exactly one reading %q", issues, "config "+coercion)
		}
	})

	t.Run("suppressed when the config category is scoped out", func(t *testing.T) {
		defer func(saved string) { doctorChecks = saved }(doctorChecks)
		// --checks containers deliberately excludes config, so a config
		// finding must not leak into the verdict.
		doctorChecks = "containers"

		filtered := filterReportByDoctorChecks(base)
		if len(filtered.ConfigCoercions) != 0 {
			t.Errorf("ConfigCoercions = %v, want none when config is excluded", filtered.ConfigCoercions)
		}
		for _, issue := range collectDoctorWarnings(filtered) {
			if strings.Contains(issue, coercion) {
				t.Errorf("config finding leaked into an excluded category: %q", issue)
			}
		}
	})
}

// TestLoadForInspectionSurvivesAConfigTheStrictLoaderRefuses pins the
// recovery route for the diagnostic commands. Registering doctor and
// `config show` as recovery commands only exempts the tunables load in
// setupGlobalOpts; each command body reads config again, and a strict read
// there would make the tools that diagnose a broken install inert on
// exactly the installs that need them.
func TestLoadForInspectionSurvivesAConfigTheStrictLoaderRefuses(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		// mutate breaks the config in a way Coerce deliberately does not
		// repair, so the strict loader genuinely refuses it.
		mutate func(map[string]any)
		// assert checks the field the operator needs to see reported.
		assert func(*testing.T, config.State)
	}{
		{
			name:   "a backend selecting where data lives",
			mutate: func(m map[string]any) { m["memory_backend"] = "mem0" },
			assert: func(t *testing.T, s config.State) {
				if s.MemoryBackend != "mem0" {
					t.Errorf("MemoryBackend = %q, want the on-disk value reported as-is", s.MemoryBackend)
				}
			},
		},
		{
			name:   "an out-of-range port",
			mutate: func(m map[string]any) { m["nats_client_port"] = 999999 },
			assert: func(t *testing.T, s config.State) {
				if s.NATSClientPort != 999999 {
					t.Errorf("NATSClientPort = %d, want the on-disk value reported as-is", s.NATSClientPort)
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()

			dir := t.TempDir()
			body := map[string]any{
				"data_dir":            dir,
				"backend_port":        3001,
				"web_port":            3000,
				"persistence_backend": "sqlite",
				"memory_backend":      "sqlvector",
				"encrypt_secrets":     false,
			}
			tt.mutate(body)
			raw, err := json.Marshal(body)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(config.StatePath(dir), raw, 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := config.Load(dir); err == nil {
				t.Fatal("fixture is not broken: the strict loader accepted it")
			}

			var stderr bytes.Buffer
			state := loadForInspection(dir, ui.NewUIWithOptions(&stderr, ui.Options{Plain: true}))

			if state.BackendPort != 3001 {
				t.Errorf("BackendPort = %d, want the readable fields intact", state.BackendPort)
			}
			tt.assert(t, state)
			if stderr.Len() == 0 {
				t.Error("a config that cannot be fully resolved must be reported, not silently degraded")
			}
		})
	}
}

func TestDoctorRejectsExtraArgs(t *testing.T) {
	// Cannot run in parallel: rootCmd is a package-level singleton.
	// sandboxRootCmd snapshots writers and bound flag values and
	// registers a t.Cleanup that restores them, so other tests in this
	// package never observe leaked SetArgs/SetOut state.
	_, _, dataDir := sandboxRootCmd(t)
	rootCmd.SetArgs([]string{"--data-dir", dataDir, "doctor", "bogus"})

	err := rootCmd.Execute()
	if err == nil {
		t.Fatal("expected error for unexpected positional arg, got nil")
	}
	msg := err.Error()
	if !strings.Contains(msg, "unknown command") && !strings.Contains(msg, "accepts 0 arg") {
		t.Fatalf("expected NoArgs rejection, got: %v", err)
	}
}
