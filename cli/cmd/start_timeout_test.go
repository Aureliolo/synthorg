package cmd

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

// newTimeoutCmd builds a bare command carrying the same --timeout flag
// start registers, plus the GlobalOpts parseStartTimeout falls back to.
func newTimeoutCmd(t *testing.T, tunable time.Duration) *cobra.Command {
	t.Helper()
	cmd := &cobra.Command{Use: "start"}
	cmd.Flags().StringVar(&startTimeout, "timeout", "90s", "health check timeout")
	tunables := config.DefaultTunables()
	tunables.HealthWaitTimeout = tunable
	cmd.SetContext(SetGlobalOpts(context.Background(), &GlobalOpts{Tunables: tunables}))
	return cmd
}

// TestParseStartTimeout covers the three-way precedence: an explicit
// --timeout wins, an unset flag falls back to the resolved tunable, and a
// non-positive tunable falls through to the shipped default rather than
// arming a zero-length wait.
func TestParseStartTimeout(t *testing.T) {
	origTimeout, origNoWait := startTimeout, startNoWait
	t.Cleanup(func() { startTimeout, startNoWait = origTimeout, origNoWait })

	cases := []struct {
		name     string
		flag     string
		setFlag  bool
		noWait   bool
		tunable  time.Duration
		want     time.Duration
		wantErr  bool
		errMatch string
	}{
		{
			name:    "unset flag uses the resolved tunable",
			tunable: 45 * time.Second,
			want:    45 * time.Second,
		},
		{
			name:    "a non-positive tunable falls back to the default",
			tunable: 0,
			want:    config.DefaultHealthWaitTimeout,
		},
		{
			name:    "an explicit flag beats the tunable",
			flag:    "2m",
			setFlag: true,
			tunable: 45 * time.Second,
			want:    2 * time.Minute,
		},
		{
			name:     "an unparseable duration is rejected",
			flag:     "soon",
			setFlag:  true,
			tunable:  45 * time.Second,
			wantErr:  true,
			errMatch: "invalid --timeout",
		},
		{
			name:     "a zero timeout is rejected while waiting",
			flag:     "0s",
			setFlag:  true,
			tunable:  45 * time.Second,
			wantErr:  true,
			errMatch: "must be > 0",
		},
		{
			// --no-wait means nothing waits, so a zero duration is not a
			// contradiction and must not fail the command.
			name:    "a zero timeout is accepted with --no-wait",
			flag:    "0s",
			setFlag: true,
			noWait:  true,
			tunable: 45 * time.Second,
			want:    0,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			startNoWait = tc.noWait
			cmd := newTimeoutCmd(t, tc.tunable)
			if tc.setFlag {
				if err := cmd.Flags().Set("timeout", tc.flag); err != nil {
					t.Fatalf("could not set --timeout: %v", err)
				}
			}

			got, err := parseStartTimeout(cmd)

			if tc.wantErr {
				if err == nil {
					t.Fatalf("parseStartTimeout() = %v, want an error", got)
				}
				if !strings.Contains(err.Error(), tc.errMatch) {
					t.Errorf("parseStartTimeout() error = %q, want it to contain %q", err, tc.errMatch)
				}
				return
			}
			if err != nil {
				t.Fatalf("parseStartTimeout() unexpected error: %v", err)
			}
			if got != tc.want {
				t.Errorf("parseStartTimeout() = %v, want %v", got, tc.want)
			}
		})
	}
}

// TestWarnIfDependenciesDegraded covers the readiness follow-up start runs
// once liveness passes. A degraded dependency is a warning the operator
// must see, and it must never be reported as a failed start.
func TestWarnIfDependenciesDegraded(t *testing.T) {
	cases := []struct {
		name       string
		status     int
		noServer   bool
		wantQuiet  bool
		wantSubstr string
	}{
		{
			name:      "a ready backend says nothing",
			status:    http.StatusOK,
			wantQuiet: true,
		},
		{
			name:       "a degraded dependency is named as a warning",
			status:     http.StatusServiceUnavailable,
			wantSubstr: "dependency is not ready",
		},
		{
			// The liveness probe passed moments ago, so an unreachable
			// backend here is new information, not the steady state.
			name:       "a backend that went away between probes is called out",
			noServer:   true,
			wantSubstr: "stopped answering",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			port := freePortWithStatus(t, tc.status, tc.noServer)
			var buf bytes.Buffer
			out := ui.NewUIWithOptions(&buf, ui.Options{NoColor: true, Hints: "auto"})

			warnIfDependenciesDegraded(
				context.Background(),
				config.State{BackendPort: port},
				out,
			)

			got := buf.String()
			if tc.wantQuiet && strings.TrimSpace(got) != "" {
				t.Errorf("warnIfDependenciesDegraded() printed %q, want nothing", got)
			}
			if tc.wantSubstr != "" && !strings.Contains(got, tc.wantSubstr) {
				t.Errorf("warnIfDependenciesDegraded() = %q, want it to contain %q", got, tc.wantSubstr)
			}
		})
	}
}

// freePortWithStatus starts a readiness stub answering with status and
// returns its port. With noServer the stub is closed first, so the port is
// bound by nothing and the probe fails to connect.
func freePortWithStatus(t *testing.T, status int, noServer bool) int {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(status)
	}))
	parsed, err := url.Parse(srv.URL)
	if err != nil {
		srv.Close()
		t.Fatalf("could not parse test server URL %q: %v", srv.URL, err)
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil {
		srv.Close()
		t.Fatalf("could not parse test server port %q: %v", parsed.Port(), err)
	}
	if noServer {
		srv.Close()
		return port
	}
	t.Cleanup(srv.Close)
	return port
}
