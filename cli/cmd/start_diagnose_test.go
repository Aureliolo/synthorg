package cmd

import (
	"encoding/json"
	"maps"
	"strings"
	"testing"
	"text/template"
	"time"
	"unicode/utf8"
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

// TestLastProbeTruncatesOnARuneBoundary pins the cut against multi-byte
// output. The limit is a byte count, so a probe carrying a non-ASCII path
// or quote can put a rune across it; emitting the broken half would put
// invalid UTF-8 on the terminal at the exact moment the operator is trying
// to read a failure.
func TestLastProbeTruncatesOnARuneBoundary(t *testing.T) {
	t.Parallel()

	// Three bytes per rune, so the boundary lands mid-rune for two out of
	// every three limits regardless of what probeOutputLimit is set to.
	long := strings.Repeat("éè", probeOutputLimit)
	payload := `{"State": {"Health": {"Log": [{"ExitCode": 1, "Output": "` + long + `"}]}}}`
	got := mustInspect(t, payload).lastProbe()
	if !utf8.ValidString(got) {
		t.Errorf("truncation produced invalid UTF-8: %q", got)
	}
	if !strings.HasSuffix(got, "...") {
		t.Errorf("truncated output must be marked as such, got %q", got)
	}
}

// TestSummariseWithoutAUsableStartTimeClaimsNoRemainingBudget covers the
// combination that would otherwise read as a lie: a container reporting
// health "starting" whose StartedAt the daemon did not supply. With
// elapsed time unknown there is no honest remaining figure, and printing
// the full grace period would tell an operator to keep waiting on a
// container that has been stuck for minutes.
func TestSummariseWithoutAUsableStartTimeClaimsNoRemainingBudget(t *testing.T) {
	t.Parallel()

	now := time.Date(2026, 7, 25, 9, 25, 0, 0, time.UTC)
	payload := `{
		"Name": "/coldboot-backend-1",
		"State": {"Status": "running", "Health": {"Status": "starting", "Log": []}},
		"Config": {"Healthcheck": {"StartPeriod": 600000000000}}
	}`
	got := mustInspect(t, payload).summarise(now)
	if !strings.Contains(got, "health starting") {
		t.Errorf("summary must still report the health status, got: %s", got)
	}
	for _, absent := range []string{"start period", "left"} {
		if strings.Contains(got, absent) {
			t.Errorf("summary must not claim %q without a usable start time\ngot: %s", absent, got)
		}
	}
}

// TestComposeContainerIDsRejectsNonIDTokens is a security boundary, not a
// tidiness one. ComposeExecOutput merges stderr into stdout, and compose
// writes routine warnings there, so `compose ps --quiet` output cannot be
// treated as a bare ID list: every token here becomes argv for `docker
// inspect`, where one beginning with `-` is parsed as a Docker persistent
// flag and `-H` retargets the daemon entirely.
func TestComposeContainerIDsRejectsNonIDTokens(t *testing.T) {
	t.Parallel()

	const shortID = "0123456789ab"
	longID := strings.Repeat("0123456789abcdef", 4)

	tests := []struct {
		name string
		raw  string
		want []string
	}{
		{
			name: "plain id list",
			raw:  shortID + "\n" + longID + "\n",
			want: []string{shortID, longID},
		},
		{
			name: "compose warning on stderr is discarded",
			raw:  "WARN[0000] Found orphan containers ([foo]) for this project\n" + shortID + "\n",
			want: []string{shortID},
		},
		{
			name: "a flag-shaped token is discarded",
			raw:  "-H tcp://attacker:2375\n" + shortID + "\n",
			want: []string{shortID},
		},
		{
			name: "an over-length or non-hex token is discarded",
			raw:  strings.Repeat("f", 65) + " nothexadecimal " + shortID,
			want: []string{shortID},
		},
		{
			name: "no ids at all",
			raw:  "WARN[0000] the \"version\" attribute is obsolete\n",
			want: []string{},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := composeContainerIDs(tt.raw)
			if len(got) != len(tt.want) {
				t.Fatalf("ids = %v, want %v", got, tt.want)
			}
			for i := range got {
				if got[i] != tt.want[i] {
					t.Errorf("ids[%d] = %q, want %q", i, got[i], tt.want[i])
				}
			}
		})
	}
}

// TestParseInspectLinesDegradesPerLine pins the degradation contract: one
// container the daemon described in a way this binary cannot decode must
// cost exactly that one container, not the whole diagnostic. The skipped
// count is what lets the caller say so rather than presenting a short
// report as a complete one.
func TestParseInspectLinesDegradesPerLine(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		raw         string
		wantNames   []string
		wantSkipped int
	}{
		{
			name:      "well-formed lines",
			raw:       `{"Name": "/a"}` + "\n" + `{"Name": "/b"}`,
			wantNames: []string{"/a", "/b"},
		},
		{
			name:        "a malformed line costs only its own container",
			raw:         `{"Name": "/a"}` + "\n" + `{not json` + "\n" + `{"Name": "/c"}`,
			wantNames:   []string{"/a", "/c"},
			wantSkipped: 1,
		},
		{
			name:      "blank lines are not containers",
			raw:       "\n\n" + `{"Name": "/a"}` + "\n   \n",
			wantNames: []string{"/a"},
		},
		{
			name: "empty output",
			raw:  "",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			found, skipped := parseInspectLines(tt.raw)
			if skipped != tt.wantSkipped {
				t.Errorf("skipped = %d, want %d", skipped, tt.wantSkipped)
			}
			if len(found) != len(tt.wantNames) {
				t.Fatalf("found %d containers, want %d", len(found), len(tt.wantNames))
			}
			for i, want := range tt.wantNames {
				if found[i].Name != want {
					t.Errorf("found[%d].Name = %q, want %q", i, found[i].Name, want)
				}
			}
		})
	}
}

// TestCrashLoopDetection covers the one failure compose's dependency wait
// cannot end on its own. A restart resets the health start period, so a
// container failing faster than that period reports "starting" forever and
// never goes unhealthy; `compose up -d` would block on it indefinitely
// while the operator watched a spinner. The restart count is what
// separates that from the slow cold boot this budget exists to allow.
func TestCrashLoopDetection(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		containers []containerInspect
		wantName   string
	}{
		{
			name:       "no containers yet",
			containers: nil,
		},
		{
			name: "a slow boot is not a crash loop",
			containers: []containerInspect{
				withHealth(inspectFixture("/stack-backend-1", 0), "starting"),
			},
		},
		{
			name: "one restart is recoverable, not a loop",
			containers: []containerInspect{
				withHealth(inspectFixture("/stack-backend-1", crashLoopRestartThreshold-1), "starting"),
			},
		},
		{
			name: "repeated restarts without health is a loop",
			containers: []containerInspect{
				withHealth(inspectFixture("/stack-backend-1", crashLoopRestartThreshold), "starting"),
			},
			wantName: "/stack-backend-1",
		},
		{
			name: "restarts after reaching healthy are a different problem",
			containers: []containerInspect{
				withHealth(inspectFixture("/stack-backend-1", 9), "healthy"),
			},
		},
		{
			// A container with no healthcheck at all still counts: it can
			// never report healthy, so restarts are the only signal there is.
			name: "a container without a healthcheck still counts",
			containers: []containerInspect{
				inspectFixture("/stack-nats-1", crashLoopRestartThreshold+3),
			},
			wantName: "/stack-nats-1",
		},
		{
			name: "the looping container is picked out of a healthy stack",
			containers: []containerInspect{
				withHealth(inspectFixture("/stack-postgres-1", 0), "healthy"),
				withHealth(inspectFixture("/stack-backend-1", crashLoopRestartThreshold), "starting"),
			},
			wantName: "/stack-backend-1",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := crashLoopingContainer(tt.containers)
			if tt.wantName == "" {
				if got != nil {
					t.Errorf("got %q, want no crash loop reported", got.Name)
				}
				return
			}
			if got == nil {
				t.Fatalf("no crash loop reported, want %q", tt.wantName)
			}
			if got.Name != tt.wantName {
				t.Errorf("reported %q, want %q", got.Name, tt.wantName)
			}
		})
	}
}

// TestStartFailureHint covers the advice that answers the reported
// problem. Compose reports "container ... is unhealthy" for a container
// that has not failed at all, and the operator's correct move differs
// completely between the two cases this distinguishes.
func TestStartFailureHint(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		containers []containerInspect
		wantSaid   string
		wantSilent bool
	}{
		{
			name:       "nothing to say about an empty stack",
			containers: nil,
			wantSilent: true,
		},
		{
			name: "a still-starting container means wait",
			containers: []containerInspect{
				withHealth(inspectFixture("/stack-backend-1", 0), "starting"),
			},
			wantSaid: "has not finished booting",
		},
		{
			// The same "starting" status, opposite advice: waiting cannot
			// help a container that restarts before its budget elapses.
			name: "a crash loop takes precedence over the wait advice",
			containers: []containerInspect{
				withHealth(inspectFixture("/stack-backend-1", crashLoopRestartThreshold), "starting"),
			},
			wantSaid: "Waiting will not help",
		},
		{
			// A container that exited is neither: the summary line already
			// carries the exit code, and inventing advice would be guessing.
			name: "an exited container gets no invented advice",
			containers: []containerInspect{
				inspectFixture("/stack-backend-1", 0),
			},
			wantSilent: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := startFailureHint(tt.containers)
			if tt.wantSilent {
				if got != "" {
					t.Errorf("hint = %q, want none", got)
				}
				return
			}
			if !strings.Contains(got, tt.wantSaid) {
				t.Errorf("hint = %q, want it to say %q", got, tt.wantSaid)
			}
		})
	}
}

// inspectFixture builds a container inspect record with a restart count.
func inspectFixture(name string, restarts int) containerInspect {
	var c containerInspect
	c.Name = name
	c.RestartCount = restarts
	c.State.Status = "running"
	return c
}

// withHealth attaches a health status to a fixture.
func withHealth(c containerInspect, status string) containerInspect {
	c.State.Health = &containerHealth{Status: status}
	return c
}

// dockerTemplateFuncs is the FuncMap `docker inspect --format` exposes,
// on top of text/template's builtins: docker/cli's `basicFunctions`.
// Nothing else is available. Sprig helpers in particular are NOT --
// `dict` reads as though it should work and fails to parse instead.
//
// Only the signatures matter here; the test asserts the template parses
// and produces decodable output, not what these return.
var dockerTemplateFuncs = template.FuncMap{
	"json":     func(any) string { return "null" },
	"split":    strings.Split,
	"join":     strings.Join,
	"title":    strings.ToTitle,
	"lower":    strings.ToLower,
	"upper":    strings.ToUpper,
	"pad":      func(s string, _ int) string { return s },
	"truncate": func(s string, _ int) string { return s },
	"println":  func(any) string { return "" },
}

// TestInspectFormatUsesOnlyDockerTemplateFunctions is the guard that a
// pure string-contents assertion cannot be.
//
// `docker inspect --format` fails on an unknown function at PARSE time, so
// a format string referencing one never returns data: the command errors,
// inspectComposeContainers reports a failure, and every diagnostic built
// on it degrades to silence. That failure is invisible to any test that
// only greps the format string, and invisible in normal use because the
// diagnostics only run when something has ALREADY gone wrong.
func TestInspectFormatUsesOnlyDockerTemplateFunctions(t *testing.T) {
	t.Parallel()

	if _, err := template.New("inspect").Funcs(dockerTemplateFuncs).Parse(inspectFormat); err != nil {
		t.Fatalf(
			"inspectFormat does not parse against Docker's template functions: %v\n"+
				"Docker exposes only json/split/join/title/lower/upper/pad/truncate/println; "+
				"a Sprig helper such as `dict` fails here exactly as it would at runtime.",
			err,
		)
	}
}

// TestInspectFormatRoundTripsAContainer executes the format over a
// docker-shaped payload and decodes the result, so the projection is
// pinned to what summarise actually reads rather than to the literal text
// of the template.
func TestInspectFormatRoundTripsAContainer(t *testing.T) {
	t.Parallel()

	// Shaped like `docker inspect` output, including a secret-bearing
	// Config.Env the projection must not carry.
	const secret = "MASTER_KEY=must-not-appear-in-cli-memory"
	source := map[string]any{
		"Name":         "/stack-backend-1",
		"RestartCount": 7,
		"State": map[string]any{
			"Status":    "running",
			"ExitCode":  0,
			"StartedAt": "2026-07-25T09:21:00Z",
			"OOMKilled": false,
			"Health": map[string]any{
				"Status":        "starting",
				"FailingStreak": 2,
				"Log":           []map[string]any{{"ExitCode": 1, "Output": "connection refused"}},
			},
		},
		"Config": map[string]any{
			"Env":         []string{secret},
			"Healthcheck": map[string]any{"StartPeriod": int64(600_000_000_000)},
		},
	}

	// A real `json` implementation, unlike the parse-only stub above.
	funcs := template.FuncMap{}
	maps.Copy(funcs, dockerTemplateFuncs)
	funcs["json"] = func(v any) string {
		encoded, err := json.Marshal(v)
		if err != nil {
			t.Fatalf("marshal %v: %v", v, err)
		}
		return string(encoded)
	}

	tmpl, err := template.New("inspect").Funcs(funcs).Parse(inspectFormat)
	if err != nil {
		t.Fatalf("parse inspectFormat: %v", err)
	}
	var rendered strings.Builder
	if err := tmpl.Execute(&rendered, source); err != nil {
		t.Fatalf("execute inspectFormat: %v", err)
	}

	if strings.Contains(rendered.String(), secret) {
		t.Errorf("projection pulled Config.Env into CLI memory:\n%s", rendered.String())
	}

	found, skipped := parseInspectLines(rendered.String())
	if skipped != 0 || len(found) != 1 {
		t.Fatalf("rendered output did not decode: %d found, %d skipped\n%s",
			len(found), skipped, rendered.String())
	}
	got := found[0]
	if got.Name != "/stack-backend-1" {
		t.Errorf("Name = %q", got.Name)
	}
	if got.RestartCount != 7 {
		t.Errorf("RestartCount = %d, want 7", got.RestartCount)
	}
	if got.State.Health == nil || got.State.Health.Status != "starting" {
		t.Errorf("health did not survive the projection: %+v", got.State.Health)
	}
	if got.State.StartedAt != "2026-07-25T09:21:00Z" {
		t.Errorf("StartedAt = %q", got.State.StartedAt)
	}
	if got.startPeriod() != 600*time.Second {
		t.Errorf("startPeriod = %v, want 10m0s", got.startPeriod())
	}
	if probe := got.lastProbe(); probe != "connection refused" {
		t.Errorf("lastProbe = %q", probe)
	}
}
