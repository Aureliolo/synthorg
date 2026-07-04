package cmd

import (
	"bytes"
	"context"
	"encoding/json"
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
	// and attempt the probe, NOT abort with a "loading config" error. The
	// probe is stubbed so the outcome never depends on whether a backend
	// happens to be listening on the default port of the machine running
	// the tests.
	prevInterval, prevCheck := statusInterval, statusCheck
	t.Cleanup(func() { statusInterval, statusCheck = prevInterval, prevCheck })
	statusInterval = "2s"
	statusCheck = true

	prevFetch := fetchHealth
	t.Cleanup(func() { fetchHealth = prevFetch })
	probedPort := 0
	fetchHealth = func(_ context.Context, port int) ([]byte, int, error) {
		probedPort = port
		return nil, 0, errBackendUnreachable
	}

	dataDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dataDir, "config.json"), []byte("{ not json"), 0o600); err != nil {
		t.Fatalf("writing corrupt config: %v", err)
	}
	err := runStatus(statusTestCmd(t, dataDir), nil)
	if err == nil {
		t.Fatal("expected the stubbed probe error to propagate, got nil")
	}
	if strings.Contains(err.Error(), "loading config") {
		t.Errorf("--check aborted on config load instead of falling back: %v", err)
	}
	if want := config.DefaultState().BackendPort; probedPort != want {
		t.Errorf("probed port %d, want default %d", probedPort, want)
	}
}

func TestImageTag(t *testing.T) {
	t.Parallel()
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
			t.Parallel()
			if got := imageTag(tt.input); got != tt.want {
				t.Errorf("imageTag(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestFilterStatsByName(t *testing.T) {
	t.Parallel()
	const header = "NAME              CPU %   MEM USAGE / LIMIT   MEM %"
	backendRow := "synthorg-backend  1.2%    100MiB / 2GiB       5%"
	natsRow := "synthorg-nats     0.3%    20MiB / 2GiB        1%"
	strayRow := "unrelated-box     0.5%    50MiB / 2GiB        2%"

	t.Run("keeps header plus matching rows, drops strangers", func(t *testing.T) {
		t.Parallel()
		statsOut := strings.Join([]string{header, backendRow, strayRow, natsRow}, "\n") + "\n"
		names := map[string]struct{}{"synthorg-backend": {}, "synthorg-nats": {}}
		got := filterStatsByName(statsOut, names)
		want := strings.Join([]string{header, backendRow, natsRow}, "\n")
		if got != want {
			t.Errorf("filterStatsByName mismatch\n got:\n%s\nwant:\n%s", got, want)
		}
	})

	t.Run("returns empty when no data row matches", func(t *testing.T) {
		t.Parallel()
		statsOut := strings.Join([]string{header, strayRow}, "\n") + "\n"
		names := map[string]struct{}{"synthorg-backend": {}}
		if got := filterStatsByName(statsOut, names); got != "" {
			t.Errorf("expected empty result, got %q", got)
		}
	})

	t.Run("returns empty on empty input", func(t *testing.T) {
		t.Parallel()
		if got := filterStatsByName("", map[string]struct{}{"x": {}}); got != "" {
			t.Errorf("expected empty result, got %q", got)
		}
	})
}

func TestHealthIcon(t *testing.T) {
	t.Parallel()
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
			t.Parallel()
			if got := healthIcon(tt.state, tt.health); got != tt.want {
				t.Errorf("healthIcon(%q, %q) = %q, want %q", tt.state, tt.health, got, tt.want)
			}
		})
	}
}

func TestParseContainerJSON(t *testing.T) {
	t.Parallel()
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
	t.Parallel()
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
// readiness-only, and combinations where the higher severity must win.
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
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
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
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
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
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
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
				healthErr:           errBackendUnreachable,
			},
			wantLevel:    statusLevelCritical,
			wantHasIssue: "unreachable",
		},
		{
			name: "readyz unavailable -> critical dependency failure",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "healthy"},
				},
				servicesFilterEmpty: true,
				healthStatusCode:    503,
				healthEnvelopeOK:    true,
				healthData:          healthResponse{Status: "unavailable", Version: "0.0.1"},
			},
			wantLevel:    statusLevelCritical,
			wantHasIssue: "dependency",
		},
		{
			name: "no containers running -> critical",
			snap: statusSnapshot{
				containers:          nil,
				servicesFilterEmpty: true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
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
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
			},
			wantLevel: statusLevelCritical,
		},
		{
			name: "services filter matches no containers -> OK (no false critical)",
			snap: statusSnapshot{
				containers: []containerInfo{
					{Service: "backend", State: "running", Health: "healthy"},
				},
				servicesFilterEmpty: false, // user passed --services=missing
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
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
		t.Run(tc.filter+"/"+tc.svc, func(t *testing.T) {
			statusServices = tc.filter
			if got := filterAllowsService(tc.svc); got != tc.want {
				t.Errorf("filter=%q svc=%q -> %v, want %v", tc.filter, tc.svc, got, tc.want)
			}
		})
	}
}

// errBackendUnreachable is a sentinel error used by TestComputeVerdict
// to simulate a health.Fetch failure without touching the network.
// Defined as a package var so other status tests can reuse it.
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
	t.Parallel()
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
			t.Parallel()
			if got := formatUptime(tt.seconds); got != tt.want {
				t.Errorf("formatUptime(%v) = %q, want %q", tt.seconds, got, tt.want)
			}
		})
	}
}

// TestRenderHealthSectionJSON locks down that --json health output is a
// single well-formed JSON document, not a label line plus a raw byte
// dump. A regression here silently breaks every scripted consumer that
// decodes `synthorg status --json`.
func TestRenderHealthSectionJSON(t *testing.T) {
	t.Parallel()
	okHealth := healthResponse{Status: "ok", Version: "1.2.3", Uptime: 90}

	tests := []struct {
		name      string
		snap      statusSnapshot
		wantReady bool
	}{
		{
			name: "ready",
			snap: statusSnapshot{
				healthStatusCode: 200,
				healthEnvelopeOK: true,
				healthData:       okHealth,
				healthBody:       []byte(`{"data":{"status":"ok","version":"1.2.3","uptime_seconds":90}}`),
			},
			wantReady: true,
		},
		{
			name: "unreachable",
			snap: statusSnapshot{
				healthErr: errBackendUnreachable,
			},
			wantReady: false,
		},
		{
			name: "unparseable",
			snap: statusSnapshot{
				healthStatusCode: 502,
				healthBody:       []byte("<html>bad gateway</html>"),
			},
			wantReady: false,
		},
		{
			name: "not ready",
			snap: statusSnapshot{
				healthStatusCode: 503,
				healthEnvelopeOK: true,
				healthData:       healthResponse{Status: "unavailable"},
				healthBody:       []byte(`{"data":{"status":"unavailable"}}`),
			},
			wantReady: false,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			var buf bytes.Buffer
			out := ui.NewUI(&buf)
			renderHealthSectionJSON(out, tc.snap)

			var decoded healthSectionJSON
			if err := json.Unmarshal(buf.Bytes(), &decoded); err != nil {
				t.Fatalf("--json health output is not valid JSON: %v\noutput: %s", err, buf.String())
			}
			if decoded.Ready != tc.wantReady {
				t.Errorf("ready = %v, want %v (output: %s)", decoded.Ready, tc.wantReady, buf.String())
			}
			if tc.snap.healthErr != nil && decoded.Error == "" {
				t.Errorf("expected a non-empty error field, got %s", buf.String())
			}
			if tc.snap.healthEnvelopeOK {
				// Data must decode directly to healthResponse: it must
				// NOT be double-nested under another "data" key from
				// re-wrapping the raw ApiResponse envelope.
				var data healthResponse
				if err := json.Unmarshal(decoded.Data, &data); err != nil {
					t.Fatalf("decoded.Data is not a flat healthResponse: %v\ndata: %s", err, decoded.Data)
				}
				if data != tc.snap.healthData {
					t.Errorf("decoded.Data = %+v, want %+v", data, tc.snap.healthData)
				}
			}
		})
	}
}

// TestRenderContainersSectionJSON locks down that a container-query
// failure survives into --json output instead of rendering as an
// indistinguishable empty array (the #1 finding this test guards).
func TestRenderContainersSectionJSON(t *testing.T) {
	// Not t.Parallel(): mutates the package-level statusServices global
	// below, which TestRenderTopBanner also mutates -- running both in
	// Go's parallel phase races on that shared write.
	oldServices := statusServices
	t.Cleanup(func() { statusServices = oldServices })
	statusServices = ""

	tests := []struct {
		name      string
		snap      statusSnapshot
		wantErr   bool
		wantCount int
	}{
		{
			name: "containers present, no error",
			snap: statusSnapshot{
				containers: []containerInfo{{Name: "a", Service: "backend"}},
			},
			wantErr:   false,
			wantCount: 1,
		},
		{
			name: "container query failed",
			snap: statusSnapshot{
				containerErr: errBackendUnreachable,
			},
			wantErr:   true,
			wantCount: 0,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			var buf bytes.Buffer
			out := ui.NewUI(&buf)
			renderContainersSection(out, tc.snap, true)

			var decoded struct {
				Containers []containerInfo `json:"containers"`
				Error      string          `json:"error"`
			}
			if err := json.Unmarshal(buf.Bytes(), &decoded); err != nil {
				t.Fatalf("--json containers output is not valid JSON: %v\noutput: %s", err, buf.String())
			}
			if tc.wantErr && decoded.Error == "" {
				t.Errorf("expected the container query error to survive into JSON, got %s", buf.String())
			}
			if !tc.wantErr && decoded.Error != "" {
				t.Errorf("unexpected error in JSON: %q", decoded.Error)
			}
			if len(decoded.Containers) != tc.wantCount {
				t.Errorf("containers count = %d, want %d", len(decoded.Containers), tc.wantCount)
			}
		})
	}
}

// TestRenderHealthSectionBackend covers the human-readable health line
// for each reachability/readiness outcome.
func TestRenderHealthSectionBackend(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name       string
		snap       statusSnapshot
		wantSubstr string
	}{
		{
			name:       "unreachable",
			snap:       statusSnapshot{healthErr: errBackendUnreachable},
			wantSubstr: "unreachable",
		},
		{
			name:       "unparseable",
			snap:       statusSnapshot{healthStatusCode: 502},
			wantSubstr: "unparseable",
		},
		{
			name: "ready",
			snap: statusSnapshot{
				healthStatusCode: 200,
				healthEnvelopeOK: true,
				healthData:       healthResponse{Status: "ok", Version: "1.2.3"},
			},
			wantSubstr: "healthy",
		},
		{
			name: "not ready",
			snap: statusSnapshot{
				healthStatusCode: 503,
				healthEnvelopeOK: true,
				healthData:       healthResponse{Status: "unavailable"},
			},
			wantSubstr: "not ready",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			var buf bytes.Buffer
			out := ui.NewUI(&buf)
			renderHealthSectionBackend(out, tc.snap)
			if !strings.Contains(strings.ToLower(buf.String()), tc.wantSubstr) {
				t.Errorf("output %q does not contain %q", buf.String(), tc.wantSubstr)
			}
		})
	}
}

// TestRenderTopBanner covers the OK/degraded/critical banner shapes.
func TestRenderTopBanner(t *testing.T) {
	// Not t.Parallel(): mutates the package-level statusServices global
	// below, which TestRenderContainersSectionJSON also mutates -- running
	// both in Go's parallel phase races on that shared write.
	okHealth := healthResponse{Status: "ok"}

	tests := []struct {
		name       string
		snap       statusSnapshot
		wantSubstr string
	}{
		{
			name: "all green collapses to a single success line",
			snap: statusSnapshot{
				containers:          []containerInfo{{Service: "backend", State: "running", Health: "healthy"}},
				servicesFilterEmpty: true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
			},
			wantSubstr: "operational",
		},
		{
			name: "unhealthy container renders the critical box",
			snap: statusSnapshot{
				containers:          []containerInfo{{Service: "backend", State: "running", Health: "unhealthy"}},
				servicesFilterEmpty: true,
				healthStatusCode:    200,
				healthEnvelopeOK:    true,
				healthData:          okHealth,
			},
			wantSubstr: "CRITICAL",
		},
	}

	oldServices := statusServices
	t.Cleanup(func() { statusServices = oldServices })
	statusServices = ""

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var buf bytes.Buffer
			out := ui.NewUI(&buf)
			renderTopBanner(out, tc.snap)
			if !strings.Contains(buf.String(), tc.wantSubstr) {
				t.Errorf("output %q does not contain %q", buf.String(), tc.wantSubstr)
			}
		})
	}
}
