package cmd

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/verify"
)

func TestEmitFineTuneSizeHint(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name        string
		state       config.State
		wantContain []string
		wantAbsent  []string
	}{
		{
			name:        "disabled emits nothing",
			state:       config.State{FineTuning: false},
			wantAbsent:  []string{"Fine-tune"},
			wantContain: nil,
		},
		{
			name:        "gpu default variant warns about size and duration",
			state:       config.State{FineTuning: true},
			wantContain: []string{"Fine-tune image", "4 GB", "on disk", "10-30 minutes"},
		},
		{
			name:        "cpu variant uses the smaller figure",
			state:       config.State{FineTuning: true, FineTuningVariant: config.FineTuneVariantCPU},
			wantContain: []string{"Fine-tune image", "1.7 GB"},
			wantAbsent:  []string{"10-30 minutes"},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			var buf bytes.Buffer
			// Hints: "auto" (the default mode): the hint must reach ordinary
			// users, so it has to be a HintNextStep, not a HintGuidance that
			// auto would suppress.
			out := ui.NewUIWithOptions(&buf, ui.Options{NoColor: true, Hints: "auto"})

			emitFineTuneSizeHint(tc.state, out)

			got := buf.String()
			for _, want := range tc.wantContain {
				if !strings.Contains(got, want) {
					t.Errorf("hint missing %q\n--- got ---\n%s", want, got)
				}
			}
			for _, absent := range tc.wantAbsent {
				if strings.Contains(got, absent) {
					t.Errorf("hint unexpectedly contains %q\n--- got ---\n%s", absent, got)
				}
			}
		})
	}
}

func TestSandboxImageRef(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name  string
		state config.State
		want  string
	}{
		{
			name: "tag fallback when no verified digest",
			state: config.State{
				ImageTag: "latest",
			},
			want: "ghcr.io/aureliolo/synthorg-sandbox:latest",
		},
		{
			name: "tag fallback when digest map missing sandbox key",
			state: config.State{
				ImageTag: "v0.6.5",
				VerifiedDigests: map[string]string{
					"backend": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
				},
			},
			want: "ghcr.io/aureliolo/synthorg-sandbox:v0.6.5",
		},
		{
			name: "digest pin when verified digest present",
			state: config.State{
				ImageTag: "latest",
				VerifiedDigests: map[string]string{
					"sandbox": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
				},
			},
			want: "ghcr.io/aureliolo/synthorg-sandbox@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
		},
		{
			name: "empty digest falls back to tag",
			state: config.State{
				ImageTag: "latest",
				VerifiedDigests: map[string]string{
					"sandbox": "",
				},
			},
			want: "ghcr.io/aureliolo/synthorg-sandbox:latest",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got := verify.FormatImageRef("sandbox", tc.state.ImageTag, tc.state.VerifiedDigests["sandbox"])
			if got != tc.want {
				t.Errorf("sandboxImageRef = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestDockerRunQuiet(t *testing.T) {
	t.Parallel()

	// Use a cross-platform no-op command (`go version`) as a stand-in for
	// the docker binary so the test stays hermetic without a real Docker
	// daemon. info.DockerPath points at any resolvable executable.
	info := docker.Info{DockerPath: "go"}

	t.Run("command succeeds returns nil", func(t *testing.T) {
		t.Parallel()
		ctx := context.Background()
		if err := dockerRunQuiet(ctx, info, "version"); err != nil {
			t.Errorf("expected nil error from successful invocation, got %v", err)
		}
	})

	t.Run("command failure wraps sanitized stderr", func(t *testing.T) {
		t.Parallel()
		ctx := context.Background()
		err := dockerRunQuiet(ctx, info, "not-a-real-subcommand-xyzzy")
		if err == nil {
			t.Fatal("expected error from bad subcommand")
		}
		msg := err.Error()
		// Output should be present and printable (sanitizer strips control bytes).
		for _, r := range msg {
			if r < 0x20 && r != '\n' {
				t.Errorf("error message contains control byte 0x%02x: %q", r, msg)
			}
		}
	})

	t.Run("empty DockerPath falls back to PATH lookup", func(t *testing.T) {
		t.Parallel()
		ctx := context.Background()
		// Info with empty DockerPath -- the function must fall back to the
		// literal "docker". We do not assume docker is installed on the
		// test host, so the expected outcomes are (1) exec.LookPath fails
		// because no docker binary is present, or (2) docker rejects the
		// bogus arg with a non-zero exit. Either proves the fallback path
		// ran; a nil error would indicate the fallback is broken.
		emptyInfo := docker.Info{DockerPath: ""}
		err := dockerRunQuiet(ctx, emptyInfo, "__this_arg_is_not_valid__")
		if err == nil {
			t.Error("expected error from empty DockerPath fallback with bogus arg")
		}
	})

	t.Run("cancelled context propagates", func(t *testing.T) {
		t.Parallel()
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		err := dockerRunQuiet(ctx, info, "version")
		if err == nil {
			t.Fatal("expected error when context is already cancelled")
		}
		// exec.CommandContext surfaces cancellation via errors.Is when the
		// child process is killed before writing output; accept either the
		// direct ctx.Err() or an exec error string that references the
		// cancellation.
		if !errors.Is(err, context.Canceled) && !strings.Contains(err.Error(), "killed") && !strings.Contains(err.Error(), "canceled") {
			t.Errorf("expected cancellation-related error, got: %v", err)
		}
	})
}

func TestDockerPullWithRetryBackoff(t *testing.T) {
	// attempts + baseDelay are now function parameters (HYG-2): the
	// caller in start.go threads them from the resolved
	// config.Tunables, and tests supply their own deterministic
	// values without mutating package-level state.
	const (
		testAttempts  = 3
		testBaseDelay = 5 * time.Millisecond
	)

	// Force failure by pointing DockerPath at a non-existent binary.
	info := docker.Info{DockerPath: "/nonexistent/docker-binary-that-does-not-exist"}

	start := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := dockerPullWithRetry(ctx, info, "fake-image:latest", testAttempts, testBaseDelay, nil)
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("expected error when docker binary is missing")
	}
	// With attempts=3 and base backoff 5ms, expected waits are ~5ms + ~10ms = ~15ms.
	// Allow generous slack for scheduling.
	if elapsed < 10*time.Millisecond {
		t.Errorf("retry backoff not applied: elapsed=%v, expected at least 10ms", elapsed)
	}
}

func TestComputePullBackoff(t *testing.T) {
	// Direct unit tests for the overflow-guarded exponential backoff
	// helper.  HYG-2 introduced the 5-minute saturation ceiling to
	// guard against int64 overflow at high attempt counts; without
	// dedicated coverage, a regression in the shift-loop math would
	// only surface via the integration retry test which is
	// deliberately capped at 2s for speed.
	const ceiling = 5 * time.Minute

	tests := []struct {
		name      string
		baseDelay time.Duration
		attempt   int
		want      time.Duration
	}{
		{"attempt_1_no_shift", 2 * time.Second, 1, 2 * time.Second},
		{"attempt_2_doubles", 2 * time.Second, 2, 4 * time.Second},
		{"attempt_3_quadruples", 2 * time.Second, 3, 8 * time.Second},
		{"attempt_4_octuples", 2 * time.Second, 4, 16 * time.Second},
		{"attempt_8_saturates_below_ceiling", 2 * time.Second, 8, 256 * time.Second},
		{"attempt_9_hits_ceiling", 2 * time.Second, 9, ceiling},
		{"attempt_max_saturates", 2 * time.Second, 100, ceiling},
		{"zero_base_collapses_to_ceiling", 0, 1, ceiling},
		{"negative_base_collapses_to_ceiling", -time.Second, 5, ceiling},
		{"zero_attempt_is_treated_as_one", 2 * time.Second, 0, 2 * time.Second},
		{"negative_attempt_is_treated_as_one", 2 * time.Second, -5, 2 * time.Second},
		{"small_base_small_attempt", 10 * time.Millisecond, 3, 40 * time.Millisecond},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := computePullBackoff(tc.baseDelay, tc.attempt)
			if got != tc.want {
				t.Errorf("computePullBackoff(%v, %d) = %v, want %v",
					tc.baseDelay, tc.attempt, got, tc.want)
			}
			if got > ceiling {
				t.Errorf("computePullBackoff exceeded ceiling: got %v", got)
			}
			if got < 0 {
				t.Errorf("computePullBackoff returned negative duration (overflow?): %v", got)
			}
		})
	}
}
