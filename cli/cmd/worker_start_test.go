package cmd

import (
	"bytes"
	"context"
	"slices"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/spf13/cobra"
)

func TestValidateNATSURL(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		url     string
		wantErr bool
	}{
		{name: "plain nats scheme", url: "nats://localhost:4222", wantErr: false},
		{name: "tls scheme", url: "tls://nats-prod:4222", wantErr: false},
		{name: "nats+tls scheme", url: "nats+tls://nats-prod:4222", wantErr: false},
		{name: "with credentials", url: "nats://user:pass@host:4222", wantErr: false},
		{name: "no port is fine", url: "nats://localhost", wantErr: false},
		{name: "empty", url: "", wantErr: true},
		{name: "no scheme", url: "localhost:4222", wantErr: true},
		{name: "wrong scheme", url: "http://host:4222", wantErr: true},
		{name: "no host", url: "nats://", wantErr: true},
		// Regression: url.Parse accepts "nats://:4222" with Host = ":4222"
		// so the old `parsed.Host == ""` check missed it.
		{name: "port without host rejected", url: "nats://:4222", wantErr: true},
		{name: "port zero rejected", url: "nats://localhost:0", wantErr: true},
		{name: "port over 65535 rejected", url: "nats://localhost:70000", wantErr: true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			err := validateNATSURL(tc.url)
			if (err != nil) != tc.wantErr {
				t.Errorf("validateNATSURL(%q) error=%v, wantErr=%v", tc.url, err, tc.wantErr)
			}
		})
	}
}

func TestValidateContainerName(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		input   string
		wantErr bool
	}{
		{name: "empty allowed (default)", input: "", wantErr: false},
		{name: "alphanumeric", input: "synthorg-backend", wantErr: false},
		{name: "with underscore", input: "synthorg_backend", wantErr: false},
		{name: "with dot", input: "synthorg.backend", wantErr: false},
		{name: "semicolon rejected", input: "backend;rm", wantErr: true},
		{name: "space rejected", input: "back end", wantErr: true},
		{name: "backtick rejected", input: "back`end", wantErr: true},
		{name: "dollar rejected", input: "back$end", wantErr: true},
		// A leading hyphen is read by docker's own flag parser as an
		// option rather than as the CONTAINER positional, so it would
		// inject arbitrary docker exec flags ahead of the fixed command.
		{name: "leading hyphen rejected", input: "-privileged", wantErr: true},
		{name: "leading double hyphen rejected", input: "--user=root", wantErr: true},
		{name: "leading underscore rejected", input: "_backend", wantErr: true},
		{name: "leading dot rejected", input: ".backend", wantErr: true},
		{name: "interior hyphen allowed", input: "synthorg-backend-1", wantErr: false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			err := validateContainerName(tc.input)
			if (err != nil) != tc.wantErr {
				t.Errorf("validateContainerName(%q) error=%v, wantErr=%v", tc.input, err, tc.wantErr)
			}
		})
	}
}

func TestRedactNATSURL(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name         string
		input        string
		mustNotHave  []string
		mustContain  []string
		exactMatch   string
		useExactOnly bool
	}{
		{
			name:         "plain url passes through",
			input:        "nats://localhost:4222",
			exactMatch:   "nats://localhost:4222",
			useExactOnly: true,
		},
		{
			name:        "username and password stripped",
			input:       "nats://admin:secretpassword@nats-prod:4222",
			mustNotHave: []string{"admin", "secretpassword"},
			mustContain: []string{"***@nats-prod:4222"},
		},
		{
			name:        "username only stripped",
			input:       "nats://admin@nats-prod:4222",
			mustNotHave: []string{"admin"},
			mustContain: []string{"***@nats-prod:4222"},
		},
		{
			name:         "tls scheme preserved",
			input:        "tls://user:pw@host:4222",
			mustNotHave:  []string{"user", "pw"},
			mustContain:  []string{"tls://", "***@host:4222"},
			useExactOnly: false,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got := redactNATSURL(tc.input)
			if tc.useExactOnly {
				if got != tc.exactMatch {
					t.Errorf("redactNATSURL(%q) = %q, want %q", tc.input, got, tc.exactMatch)
				}
				return
			}
			for _, bad := range tc.mustNotHave {
				if strings.Contains(got, bad) {
					t.Errorf("redactNATSURL(%q) = %q, must not contain %q", tc.input, got, bad)
				}
			}
			for _, good := range tc.mustContain {
				if !strings.Contains(got, good) {
					t.Errorf("redactNATSURL(%q) = %q, must contain %q", tc.input, got, good)
				}
			}
		})
	}
}

func TestRunWorkerStartRejectsBadInput(t *testing.T) {
	// Can't easily test runWorkerStart directly because of cobra + global
	// flag state, but the helpers above cover the validation paths that
	// runWorkerStart calls into before invoking execDocker.
	t.Run("validators_cover_runWorkerStart_preconditions", func(t *testing.T) {
		t.Parallel()
		if err := validateNATSURL(""); err == nil {
			t.Error("expected empty URL to be rejected")
		}
		if err := validateContainerName("bad;name"); err == nil {
			t.Error("expected unsafe container name to be rejected")
		}
	})
}

func TestValidateWorkerStartPlan(t *testing.T) {
	t.Parallel()

	valid := workerStartPlan{
		natsURL:      "nats://nats:4222",
		streamPrefix: "SYNTHORG",
		container:    defaultWorkerContainer,
		workers:      4,
	}

	cases := []struct {
		name    string
		mutate  func(p *workerStartPlan)
		wantErr bool
	}{
		{name: "valid plan", mutate: func(*workerStartPlan) {}, wantErr: false},
		{name: "zero workers", mutate: func(p *workerStartPlan) { p.workers = 0 }, wantErr: true},
		{name: "negative workers", mutate: func(p *workerStartPlan) { p.workers = -1 }, wantErr: true},
		{name: "empty nats url", mutate: func(p *workerStartPlan) { p.natsURL = "" }, wantErr: true},
		{name: "bad stream prefix", mutate: func(p *workerStartPlan) { p.streamPrefix = "lower case" }, wantErr: true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			plan := valid
			tc.mutate(&plan)
			err := validateWorkerStartPlan(plan)
			if (err != nil) != tc.wantErr {
				t.Errorf("validateWorkerStartPlan(%+v) error=%v, wantErr=%v", plan, err, tc.wantErr)
			}
		})
	}
}

func TestWorkerExecArgs(t *testing.T) {
	t.Parallel()

	args := workerExecArgs(workerStartPlan{
		natsURL:      "nats://user:secret@nats:4222",
		streamPrefix: "SYNTHORG",
		container:    "synthorg-backend",
		workers:      8,
	})
	joined := strings.Join(args, " ")

	// Every forwarded name must be a bare `-e NAME`, never `-e NAME=value`:
	// a value in argv is visible to anyone reading the docker process list.
	forwarded := []string{
		config.EnvNATSURL,
		config.EnvNATSStreamPrefix,
		config.EnvWorkers,
		config.EnvWorkerHTTPTimeoutSeconds,
	}
	for i, name := range forwarded {
		found := false
		for j := range args {
			if args[j] == "-e" && j+1 < len(args) && args[j+1] == name {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("forwarded[%d] %q missing from exec args %v", i, name, args)
		}
	}
	if strings.Contains(joined, "secret") {
		t.Errorf("exec args leak the NATS credentials: %v", args)
	}
	// The `--` stops docker's own flag parsing, so the container name can
	// only ever be read as the CONTAINER positional.
	sep := -1
	for i, a := range args {
		if a == "--" {
			sep = i
			break
		}
	}
	if sep < 0 {
		t.Fatalf("exec args have no -- separator: %v", args)
	}
	if sep+1 >= len(args) || args[sep+1] != "synthorg-backend" {
		t.Errorf("container must follow the -- separator, got %v", args)
	}
	if !strings.HasSuffix(joined, "--workers 8") {
		t.Errorf("exec args must end with the worker count, got %v", args)
	}
}

// TestRunWorkerStartForwardsThroughTheEnvironment drives the whole command
// with execDocker overridden, which is what that seam exists for. The
// property is that every resolved value reaches the child through its
// environment and none of them through argv, so `docker ps` and the host
// process list cannot show the NATS credentials.
func TestRunWorkerStartForwardsThroughTheEnvironment(t *testing.T) {
	origExec := execDocker
	origCount := workerStartCount
	origURL := workerStartNATSURL
	origPrefix := workerStartStreamPrefix
	origContainer := workerStartContainer
	t.Cleanup(func() {
		execDocker = origExec
		workerStartCount = origCount
		workerStartNATSURL = origURL
		workerStartStreamPrefix = origPrefix
		workerStartContainer = origContainer
	})

	var gotArgs, gotEnv []string
	called := 0
	execDocker = func(_ context.Context, args, env []string) error {
		called++
		gotArgs, gotEnv = args, env
		return nil
	}

	// Set on the parent, not built into the child env: the exec forwards the
	// bare name, so the operator's value only crosses the boundary if it is
	// inherited. Without this the documented precedence stops at the
	// container wall and the registered default silently wins inside it.
	t.Setenv(config.EnvWorkerHTTPTimeoutSeconds, "45")

	var buf bytes.Buffer
	cmd := newWorkerStartTestCmd(&buf)
	for flag, value := range map[string]string{
		"nats-url": "nats://user:secret@nats:4222",
		"workers":  "6",
	} {
		if err := cmd.Flags().Set(flag, value); err != nil {
			t.Fatalf("could not set --%s: %v", flag, err)
		}
	}

	if err := runWorkerStart(cmd, nil); err != nil {
		t.Fatalf("runWorkerStart() error: %v", err)
	}

	if called != 1 {
		t.Fatalf("execDocker called %d times, want exactly 1", called)
	}
	if strings.Contains(strings.Join(gotArgs, " "), "secret") {
		t.Errorf("exec args leak the NATS credentials: %v", gotArgs)
	}
	if strings.Contains(buf.String(), "secret") {
		t.Errorf("printed output leaks the NATS credentials: %q", buf.String())
	}
	for _, want := range []string{
		config.EnvNATSURL + "=nats://user:secret@nats:4222",
		config.EnvNATSStreamPrefix + "=" + config.DefaultTunables().DefaultNATSStreamPrefix,
		config.EnvWorkers + "=6",
		config.EnvWorkerHTTPTimeoutSeconds + "=45",
	} {
		if !slices.Contains(gotEnv, want) {
			t.Errorf("child environment missing %q", want)
		}
	}
}

// TestResolveWorkerStartFlagsIgnoresAStaleContainerGlobal pins the
// Changed("container") guard. pflag binds --container to a package global, so
// an earlier explicit invocation leaves it set; without the guard the next
// invocation that omits the flag execs into the previous container instead of
// the default, and nothing else in this file would notice.
func TestResolveWorkerStartFlagsIgnoresAStaleContainerGlobal(t *testing.T) {
	origContainer := workerStartContainer
	t.Cleanup(func() { workerStartContainer = origContainer })

	first := newWorkerStartTestCmd(&bytes.Buffer{})
	if err := first.Flags().Set("container", "synthorg-backend-old"); err != nil {
		t.Fatalf("could not set --container: %v", err)
	}
	opts := GetGlobalOpts(first.Context())
	if got := resolveWorkerStartFlags(first, opts).container; got != "synthorg-backend-old" {
		t.Fatalf("explicit --container resolved to %q, want synthorg-backend-old", got)
	}

	// The global still holds the first invocation's value here, which is
	// precisely the state the guard has to survive.
	second := newWorkerStartTestCmd(&bytes.Buffer{})
	if got := resolveWorkerStartFlags(second, GetGlobalOpts(second.Context())).container; got != defaultWorkerContainer {
		t.Errorf("omitted --container resolved to %q, want %q", got, defaultWorkerContainer)
	}
}

// TestRunWorkerStartRefusesABadPlanBeforeExec proves validation runs before
// anything is handed to docker: a rejected plan must exec nothing at all.
func TestRunWorkerStartRefusesABadPlanBeforeExec(t *testing.T) {
	origExec := execDocker
	origCount := workerStartCount
	t.Cleanup(func() {
		execDocker = origExec
		workerStartCount = origCount
	})

	called := 0
	execDocker = func(context.Context, []string, []string) error {
		called++
		return nil
	}

	cmd := newWorkerStartTestCmd(&bytes.Buffer{})
	if err := cmd.Flags().Set("workers", "0"); err != nil {
		t.Fatalf("could not set --workers: %v", err)
	}

	if err := runWorkerStart(cmd, nil); err == nil {
		t.Fatal("runWorkerStart() accepted a zero worker count")
	}
	if called != 0 {
		t.Errorf("execDocker called %d times on a rejected plan, want 0", called)
	}
}

// newWorkerStartTestCmd builds a command bound to the same globals the real
// one binds, so runWorkerStart resolves flags exactly as it does in
// production without mutating the shared workerStartCmd.
func newWorkerStartTestCmd(out *bytes.Buffer) *cobra.Command {
	cmd := &cobra.Command{Use: "start", RunE: runWorkerStart}
	cmd.Flags().IntVar(&workerStartCount, "workers", defaultWorkerCount, "")
	cmd.Flags().StringVar(&workerStartNATSURL, "nats-url", config.DefaultNATSURLValue, "")
	cmd.Flags().StringVar(&workerStartStreamPrefix, "stream-prefix", config.DefaultNATSStreamPrefixValue, "")
	cmd.Flags().StringVar(&workerStartContainer, "container", "", "")
	cmd.SetOut(out)
	cmd.SetContext(SetGlobalOpts(context.Background(), &GlobalOpts{
		Hints:    "auto",
		Tunables: config.DefaultTunables(),
	}))
	return cmd
}
