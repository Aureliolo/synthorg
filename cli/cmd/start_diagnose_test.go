package cmd

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// mustInspect decodes a docker-inspect payload the way the real code path
// does, so the fixtures below stay in the daemon's own wire format rather
// than hand-built Go structs that could drift from it.
func mustInspect(t *testing.T, payload string) containerInspect {
	t.Helper()
	var c containerInspect
	if err := json.Unmarshal([]byte(payload), &c); err != nil {
		t.Fatalf("decode inspect payload: %v", err)
	}
	return c
}

// TestContainerSummarise covers what a start failure has to be able to
// say. Compose reports every one of these as the same "container ... is
// unhealthy", which is why the summary exists.
func TestContainerSummarise(t *testing.T) {
	t.Parallel()

	// now is fixed so uptime arithmetic is deterministic.
	now := time.Date(2026, 7, 25, 9, 25, 0, 0, time.UTC)

	tests := []struct {
		name         string
		payload      string
		wantContains []string
		wantAbsent   []string
	}{
		{
			// The reported failure: a slow cold boot that has not failed at
			// all. The operator needs to know it is still starting and how
			// much budget remains, not that it is "unhealthy".
			name: "still inside its start period",
			payload: `{
				"Name": "/coldboot-backend-1",
				"RestartCount": 0,
				"State": {
					"Status": "running",
					"StartedAt": "2026-07-25T09:21:00Z",
					"Health": {"Status": "starting", "FailingStreak": 0, "Log": []}
				},
				"Config": {"Healthcheck": {"StartPeriod": 600000000000}}
			}`,
			wantContains: []string{
				"coldboot-backend-1", "running", "running 4m0s",
				"health starting", "still inside its 10m0s start period", "6m0s left",
			},
		},
		{
			// A crash-loop presents as "starting" forever, because each
			// restart resets the start-period clock. The restart count is
			// the only thing that tells the two apart.
			name: "crash-looping container reports its restart count",
			payload: `{
				"Name": "/coldboot-backend-1",
				"RestartCount": 13,
				"State": {
					"Status": "running",
					"StartedAt": "2026-07-25T09:24:50Z",
					"Health": {
						"Status": "starting",
						"Log": [{"ExitCode": 1, "Output": "Traceback (most recent call last):\n  File x\nConnectionRefusedError: [Errno 111] Connection refused\n"}]
					}
				},
				"Config": {"Healthcheck": {"StartPeriod": 600000000000}}
			}`,
			wantContains: []string{
				"restarted 13 time(s)",
				"last health probe: ConnectionRefusedError: [Errno 111] Connection refused",
			},
			// The traceback frames above the exception line are noise.
			wantAbsent: []string{"most recent call last", "File x"},
		},
		{
			name: "exited container reports its exit code",
			payload: `{
				"Name": "/coldboot-backend-1",
				"RestartCount": 0,
				"State": {
					"Status": "exited",
					"ExitCode": 137,
					"OOMKilled": false,
					"StartedAt": "2026-07-25T09:24:00Z"
				},
				"Config": {}
			}`,
			wantContains: []string{"exited", "exit code 137"},
			wantAbsent:   []string{"OOM-killed", "health "},
		},
		{
			name: "OOM kill is called out",
			payload: `{
				"Name": "/coldboot-backend-1",
				"State": {"Status": "exited", "ExitCode": 137, "OOMKilled": true, "StartedAt": "2026-07-25T09:24:00Z"},
				"Config": {}
			}`,
			wantContains: []string{"exit code 137", "OOM-killed"},
		},
		{
			// A daemon that reports no healthcheck, no start time, and no
			// health block must still produce a usable line rather than
			// panicking or printing nonsense.
			name:         "sparse payload degrades to what is known",
			payload:      `{"Name": "/coldboot-backend-1", "State": {"Status": "created"}, "Config": {}}`,
			wantContains: []string{"coldboot-backend-1", "created"},
			wantAbsent:   []string{"running ", "start period", "restarted"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := mustInspect(t, tt.payload).summarise(now)
			for _, want := range tt.wantContains {
				if !strings.Contains(got, want) {
					t.Errorf("summary missing %q\ngot: %s", want, got)
				}
			}
			for _, absent := range tt.wantAbsent {
				if strings.Contains(got, absent) {
					t.Errorf("summary must not contain %q\ngot: %s", absent, got)
				}
			}
		})
	}
}

// TestContainerUptimeRejectsUnusableStartTimes pins the guard against a
// daemon reporting a zero or future start time: reporting a negative or
// absurd uptime in a failure summary would be worse than reporting none.
func TestContainerUptimeRejectsUnusableStartTimes(t *testing.T) {
	t.Parallel()

	now := time.Date(2026, 7, 25, 9, 25, 0, 0, time.UTC)
	tests := []struct {
		name    string
		started string
	}{
		{"zero value docker reports for a never-started container", "0001-01-01T00:00:00Z"},
		{"unparseable", "not-a-timestamp"},
		{"absent", ""},
		{"in the future (host clock skew)", "2026-07-25T09:30:00Z"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			payload := `{"State": {"StartedAt": "` + tt.started + `"}}`
			if got := mustInspect(t, payload).uptime(now); got != 0 {
				t.Errorf("uptime = %v, want 0", got)
			}
		})
	}
}

// TestLastProbeTruncatesRunawayOutput keeps a pathological probe from
// flooding the terminal on a failure the operator is trying to read.
func TestLastProbeTruncatesRunawayOutput(t *testing.T) {
	t.Parallel()

	long := strings.Repeat("x", probeOutputLimit*3)
	payload := `{"State": {"Health": {"Log": [{"ExitCode": 1, "Output": "` + long + `"}]}}}`
	got := mustInspect(t, payload).lastProbe()
	if len(got) > probeOutputLimit+len("...") {
		t.Errorf("probe output not truncated: %d chars", len(got))
	}
	if !strings.HasSuffix(got, "...") {
		t.Errorf("truncated output must be marked as such, got tail %q", got[max(0, len(got)-8):])
	}
}
