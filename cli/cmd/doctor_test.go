package cmd

import (
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/diagnostics"
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
			name: "backend unhealthy status code",
			report: diagnostics.Report{
				HealthStatus:      "503",
				ComposeFileExists: true,
				ComposeFileValid:  boolPtr(true),
			},
			wantStatus:   doctorErrors,
			wantCount:    1,
			wantContains: []string{"HTTP 503"},
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

// TestDoctorReportsConfigCoercions covers the diagnosis half of the
// config-recovery story: when the running binary does not recognise a
// persisted setting it substitutes a default, and doctor has to say so.
// Without this the operator sees a stack running on values they never
// chose, with the on-disk file still showing the originals.
func TestDoctorReportsConfigCoercions(t *testing.T) {
	base := diagnostics.Report{
		HealthStatus:      "200",
		ComposeFileExists: true,
		ContainerSummary:  []diagnostics.ContainerDetail{{Name: "backend", Health: "healthy"}},
		ConfigCoercions: []string{
			`memory_backend: "mem0" is not a recognised value, using sqlvector instead (valid: composite, inmemory, sqlvector)`,
		},
	}

	t.Run("surfaced as a warning by default", func(t *testing.T) {
		defer func(saved string) { doctorChecks = saved }(doctorChecks)
		doctorChecks = ""

		status, issues := classifyDoctor(base)
		if status != doctorWarnings {
			t.Errorf("status = %v, want %v", status, doctorWarnings)
		}
		var found bool
		for _, issue := range issues {
			if strings.Contains(issue, "memory_backend") {
				found = true
			}
		}
		if !found {
			t.Errorf("issues must mention the coerced field, got %v", issues)
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
			if strings.Contains(issue, "memory_backend") {
				t.Errorf("config finding leaked into an excluded category: %q", issue)
			}
		}
	})
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
