package cmd

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// statusTestCmd builds a cobra command whose context carries GlobalOpts
// pointing at dataDir, for exercising runStatus without the root wiring.
func statusTestCmd(t *testing.T, dataDir string) *cobra.Command {
	t.Helper()
	cmd := &cobra.Command{}
	ctx := SetGlobalOpts(context.Background(), &GlobalOpts{
		DataDir:  dataDir,
		Hints:    "auto",
		Tunables: config.DefaultTunables(),
	})
	cmd.SetContext(ctx)
	return cmd
}

func TestRunStatusValidatesIntervalBeforeCheck(t *testing.T) {
	// A malformed --interval is a usage error even in --check mode: the
	// interval is parsed and validated BEFORE the --check dispatch, so a
	// scripted `status --check --interval bogus` no longer silently ignores
	// the bad value.
	prevInterval, prevCheck := statusInterval, statusCheck
	t.Cleanup(func() { statusInterval, statusCheck = prevInterval, prevCheck })
	statusInterval = "bogus"
	statusCheck = true

	err := runStatus(statusTestCmd(t, t.TempDir()), nil)
	if err == nil || !strings.Contains(err.Error(), "invalid --interval") {
		t.Fatalf("expected invalid --interval error, got %v", err)
	}
}

func TestRunStatusCheckProbesDespiteUnloadableConfig(t *testing.T) {
	// --check is a scripted probe that must still run when the config cannot
	// be loaded: a corrupt config.json should fall back to the default port
	// and attempt the probe (yielding an unreachable exit, since no backend
	// is up in the test), NOT abort with a "loading config" error.
	prevInterval, prevCheck := statusInterval, statusCheck
	t.Cleanup(func() { statusInterval, statusCheck = prevInterval, prevCheck })
	statusInterval = "2s"
	statusCheck = true

	dataDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dataDir, "config.json"), []byte("{ not json"), 0o600); err != nil {
		t.Fatalf("writing corrupt config: %v", err)
	}
	err := runStatus(statusTestCmd(t, dataDir), nil)
	if err == nil {
		t.Fatal("expected a probe error (no backend running), got nil")
	}
	if strings.Contains(err.Error(), "loading config") {
		t.Errorf("--check aborted on config load instead of falling back: %v", err)
	}
}

func TestImageTag(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"ghcr.io/aureliolo/synthorg-backend:0.2.9", "0.2.9"},
		{"ghcr.io/aureliolo/synthorg-web:latest", "latest"},
		{"nocolon", "nocolon"},
		{"", ""},
		{"registry:5000/image:v1.0", "v1.0"},
		{"registry:5000/image", "registry:5000/image"},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			if got := imageTag(tt.input); got != tt.want {
				t.Errorf("imageTag(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestFilterStatsByName(t *testing.T) {
	const header = "NAME              CPU %   MEM USAGE / LIMIT   MEM %"
	backendRow := "synthorg-backend  1.2%    100MiB / 2GiB       5%"
	natsRow := "synthorg-nats     0.3%    20MiB / 2GiB        1%"
	strayRow := "unrelated-box     0.5%    50MiB / 2GiB        2%"

	t.Run("keeps header plus matching rows, drops strangers", func(t *testing.T) {
		statsOut := strings.Join([]string{header, backendRow, strayRow, natsRow}, "\n") + "\n"
		names := map[string]struct{}{"synthorg-backend": {}, "synthorg-nats": {}}
		got := filterStatsByName(statsOut, names)
		want := strings.Join([]string{header, backendRow, natsRow}, "\n")
		if got != want {
			t.Errorf("filterStatsByName mismatch\n got:\n%s\nwant:\n%s", got, want)
		}
	})

	t.Run("returns empty when no data row matches", func(t *testing.T) {
		statsOut := strings.Join([]string{header, strayRow}, "\n") + "\n"
		names := map[string]struct{}{"synthorg-backend": {}}
		if got := filterStatsByName(statsOut, names); got != "" {
			t.Errorf("expected empty result, got %q", got)
		}
	})

	t.Run("returns empty on empty input", func(t *testing.T) {
		if got := filterStatsByName("", map[string]struct{}{"x": {}}); got != "" {
			t.Errorf("expected empty result, got %q", got)
		}
	})
}

func TestHealthIcon(t *testing.T) {
	tests := []struct {
		state  string
		health string
		want   string
	}{
		{"running", "healthy", ui.IconSuccess},
		{"running", "unhealthy", ui.IconError},
		// Running with no docker-level healthcheck (e.g. NATS) is the
		// steady state for containers that intentionally declare no
		// probe. Treat it as success so the table doesn't permanently
		// show an "in progress" spinner.
		{"running", "", ui.IconSuccess},
		{"running", "starting", ui.IconInProgress},
		{"restarting", "", ui.IconWarning},
		{"exited", "", ui.IconError},
		{"", "", ui.IconError},
	}
	for _, tt := range tests {
		name := tt.state + "/" + tt.health
		if name == "/" {
			name = "empty/empty"
		}
		t.Run(name, func(t *testing.T) {
			if got := healthIcon(tt.state, tt.health); got != tt.want {
				t.Errorf("healthIcon(%q, %q) = %q, want %q", tt.state, tt.health, got, tt.want)
			}
		})
	}
}

func TestParseContainerJSON(t *testing.T) {
	input := `{"Name":"a","Service":"backend","State":"running","Health":"healthy","Image":"img:1.0"}
{"Name":"b","Service":"web","State":"running","Health":"","Image":"img:1.0"}
invalid json line
`
	containers, failures := parseContainerJSON(input)
	if len(containers) != 2 {
		t.Fatalf("expected 2 containers, got %d", len(containers))
	}
	if failures != 1 {
		t.Errorf("expected 1 failure, got %d", failures)
	}
	if containers[0].Service != "backend" {
		t.Errorf("first container service = %q", containers[0].Service)
	}
}

func TestParseContainerJSON_Array(t *testing.T) {
	input := `[{"Name":"a","Service":"backend","State":"running","Health":"healthy","Image":"img:1.0"},{"Name":"b","Service":"web","State":"running","Health":"","Image":"img:1.0"}]`
	containers, failures := parseContainerJSON(input)
	if len(containers) != 2 {
		t.Fatalf("expected 2 containers, got %d", len(containers))
	}
	if failures != 0 {
		t.Errorf("expected 0 failures, got %d", failures)
	}
	if containers[0].Service != "backend" {
		t.Errorf("first container service = %q", containers[0].Service)
	}
}

// TestComputeVerdict locks down the status banner verdict logic. The
// banner is the first thing the user sees on `synthorg status`; a
// regression here either silently downgrades real failures (user
// thinks everything is fine) or overstates problems (cry-wolf). Cases
// cover each escalation lane: container-only, backend-only,
// persistence-only, and combinations where the higher severity must
// win.
func TestComputeVerdict(t *testing.T) {
	// Reset module-level filter so cases that don't override see the
	// default (no filter -> all containers count).
	oldServices := statusServices
	t.Cleanup(func() { statusServices = oldServices })

	okHealth := healthResponse{Status: "ok", Version: "0.0.1"}

	tests := []struct {
		name           string
		snap           statusSnapshot
		filter         string // value to set statusServices to before running
		wantLevel      statusLevel
		wantHasIssue   string // substring expected in issues, or "" for none
		wantSummaryHas string
	}{
		{
			name: "all green collapses to OK",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "healthy"},
					{Service: "web", State: "running"},
				},
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          healthResponse{Status: "ok", Version: "0.0.1", Persistence: "postgres", MessageBus: "nats"},
				persistenceWired:    true,
				messageBusWired:     true,
				expectsPersistent:   true,
				expectsMessageBus:   true,
			},
			wantLevel:      statusLevelOK,
			wantSummaryHas: "operational",
		},
		{
			name: "unhealthy container -> critical",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "unhealthy"},
				},
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
				persistenceWired:    true,
				messageBusWired:     true,
				expectsPersistent:   true,
				expectsMessageBus:   true,
			},
			wantLevel:    statusLevelCritical,
			wantHasIssue: "unhealthy",
		},
		{
			name: "restarting only -> degraded",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "nats", State: "restarting"},
				},
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
				persistenceWired:    true,
				messageBusWired:     true,
				expectsPersistent:   true,
				expectsMessageBus:   true,
			},
			wantLevel:    statusLevelDegraded,
			wantHasIssue: "restarting",
		},
		{
			name: "backend unreachable -> critical even if containers ok",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "healthy"},
				},
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthErr:           errBackendUnreachable,
				expectsPersistent:   true,
			},
			wantLevel:    statusLevelCritical,
			wantHasIssue: "unreachable",
		},
		{
			name: "persistence not wired when expected -> critical",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "healthy"},
				},
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
				persistenceWired:    false,
				messageBusWired:     true,
				expectsPersistent:   true,
				expectsMessageBus:   true,
			},
			wantLevel:    statusLevelCritical,
			wantHasIssue: "persistence",
		},
		{
			name: "no containers running -> critical",
			snap: statusSnapshot{
				containers:          nil,
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
				persistenceWired:    true,
				messageBusWired:     true,
				expectsPersistent:   true,
				expectsMessageBus:   true,
			},
			wantLevel:    statusLevelCritical,
			wantHasIssue: "no containers",
		},
		{
			name: "unparseable health response -> critical",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "healthy"},
				},
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthEnvelopeOK:    false,
				healthStatusCode:    502,
			},
			wantLevel:    statusLevelCritical,
			wantHasIssue: "unparseable",
		},
		{
			name: "critical wins over degraded when both present",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "unhealthy"},
					{Service: "nats", State: "restarting"},
				},
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
				persistenceWired:    true,
				messageBusWired:     false, // would be degraded on its own
				expectsPersistent:   true,
				expectsMessageBus:   true,
			},
			wantLevel: statusLevelCritical,
		},
		{
			name: "internal bus install with messagebus nil -> OK (no false degraded)",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "healthy"},
				},
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
				persistenceWired:    true,
				messageBusWired:     false,
				expectsPersistent:   true,
				expectsMessageBus:   false, // internal bus -- no NATS expected
			},
			wantLevel:      statusLevelOK,
			wantSummaryHas: "operational",
		},
		{
			name: "distributed bus expected but not wired -> degraded",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "healthy"},
				},
				servicesFilterEmpty: true,
				healthFetched:       true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
				persistenceWired:    true,
				messageBusWired:     false,
				expectsPersistent:   true,
				expectsMessageBus:   true,
			},
			wantLevel:    statusLevelDegraded,
			wantHasIssue: "message bus",
		},
		{
			name: "services filter matches no containers -> OK (no false critical)",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "healthy"},
				},
				servicesFilterEmpty: false, // user passed --services=missing
				healthFetched:       true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
				persistenceWired:    true,
				messageBusWired:     true,
				expectsPersistent:   true,
				expectsMessageBus:   true,
			},
			filter:         "missing-service",
			wantLevel:      statusLevelOK,
			wantSummaryHas: "operational",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			statusServices = tc.filter
			got := computeVerdict(tc.snap)
			if got.level != tc.wantLevel {
				t.Errorf("level = %d, want %d (issues=%v)", got.level, tc.wantLevel, got.issues)
			}
			if tc.wantHasIssue != "" && !sliceContainsSubstring(got.issues, tc.wantHasIssue) {
				t.Errorf("issues=%v, want one containing %q", got.issues, tc.wantHasIssue)
			}
			if tc.wantSummaryHas != "" && !stringsContainsCI(got.summary, tc.wantSummaryHas) {
				t.Errorf("summary=%q, want substring %q", got.summary, tc.wantSummaryHas)
			}
		})
	}
}

func TestFilterAllowsService(t *testing.T) {
	old := statusServices
	t.Cleanup(func() { statusServices = old })

	cases := []struct {
		filter, svc string
		want        bool
	}{
		{"", "backend", true},                 // empty filter = allow all
		{"backend", "backend", true},          // exact match
		{"backend", "web", false},             // not in filter
		{"backend,web", "web", true},          // multi-value
		{"backend, web , nats", "nats", true}, // whitespace-tolerant
		{"backend-extra", "backend", false},   // no prefix matching
	}
	for _, tc := range cases {
		statusServices = tc.filter
		if got := filterAllowsService(tc.svc); got != tc.want {
			t.Errorf("filter=%q svc=%q -> %v, want %v", tc.filter, tc.svc, got, tc.want)
		}
	}
}

// errBackendUnreachable is a sentinel error used by TestComputeVerdict
// to simulate a Phase-0 health.Fetch failure without touching the
// network. Defined as a package var so other status tests can reuse it.
var errBackendUnreachable = &simpleError{msg: "connection refused"}

type simpleError struct{ msg string }

func (e *simpleError) Error() string { return e.msg }

func sliceContainsSubstring(items []string, sub string) bool {
	needle := strings.ToLower(sub)
	for _, item := range items {
		if strings.Contains(strings.ToLower(item), needle) {
			return true
		}
	}
	return false
}

func stringsContainsCI(haystack, needle string) bool {
	return strings.Contains(strings.ToLower(haystack), strings.ToLower(needle))
}

func TestFormatUptime(t *testing.T) {
	tests := []struct {
		seconds float64
		want    string
	}{
		{0, "0s"},
		{45, "45s"},
		{90, "1m 30s"},
		{3600, "1h 0m"},
		{12991, "3h 36m"},
		{86400, "24h 0m"},
		{-90, "-1m 30s"},
	}
	for _, tt := range tests {
		t.Run(tt.want, func(t *testing.T) {
			if got := formatUptime(tt.seconds); got != tt.want {
				t.Errorf("formatUptime(%v) = %q, want %q", tt.seconds, got, tt.want)
			}
		})
	}
}
