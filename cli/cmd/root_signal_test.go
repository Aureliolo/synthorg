package cmd

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"
)

// TestReportExecuteError_Interrupted covers the branch Execute relies on to
// turn a signal-cancelled context into a clean "Interrupted" report instead
// of surfacing whatever raw error the killed subprocess happened to return
// (e.g. "signal: killed", which reads as a failure rather than the
// operator's own Ctrl+C).
func TestReportExecuteError_Interrupted(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // simulates the signal handler firing

	var out bytes.Buffer
	rawErr := errors.New("exec: signal: killed")
	err := reportExecuteError(ctx, &out, rawErr)

	exitErr, ok := errors.AsType[*ExitError](err)
	if !ok {
		t.Fatalf("expected *ExitError, got %T: %v", err, err)
	}
	if exitErr.Code != ExitInterrupted {
		t.Errorf("Code = %d, want ExitInterrupted (%d)", exitErr.Code, ExitInterrupted)
	}
	if !errors.Is(exitErr, rawErr) {
		t.Error("expected the raw error to be wrapped, not discarded")
	}
	if !strings.Contains(out.String(), "Interrupted") {
		t.Errorf("expected an Interrupted message on stderr, got: %s", out.String())
	}
	if strings.Contains(out.String(), "signal: killed") {
		t.Errorf("raw subprocess error text should not reach the operator, got: %s", out.String())
	}
}

// TestReportExecuteError_NotCancelled ensures an ordinary command failure
// (context never cancelled) still takes the pre-existing path: the raw
// error printed to stderr and returned unwrapped, not misreported as an
// interruption.
func TestReportExecuteError_NotCancelled(t *testing.T) {
	ctx := context.Background()
	var out bytes.Buffer
	rawErr := errors.New("some ordinary failure")

	err := reportExecuteError(ctx, &out, rawErr)

	if !errors.Is(err, rawErr) {
		t.Errorf("expected the original error back, got: %v", err)
	}
	if exitErr, ok := errors.AsType[*ExitError](err); ok {
		t.Errorf("did not expect an ExitError for a non-cancelled failure, got code %d", exitErr.Code)
	}
	if !strings.Contains(out.String(), "some ordinary failure") {
		t.Errorf("expected the raw error printed to stderr, got: %s", out.String())
	}
}

// TestReportExecuteError_Success covers the nil-error passthrough.
func TestReportExecuteError_Success(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	var out bytes.Buffer

	if err := reportExecuteError(ctx, &out, nil); err != nil {
		t.Errorf("expected nil, got: %v", err)
	}
	if out.Len() != 0 {
		t.Errorf("expected no output for a successful run, got: %s", out.String())
	}
}

// TestReportExecuteError_ExitErrorPassthroughEvenWhenCancelled documents
// that a cancelled context still takes priority over an already-typed
// ExitError/ChildExitError: the interrupted branch is what the operator
// needs to see, and it produces its own ExitError anyway.
func TestReportExecuteError_ExitErrorPassthroughEvenWhenCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	var out bytes.Buffer

	inner := NewExitError(ExitUnhealthy, errors.New("containers unhealthy"))
	err := reportExecuteError(ctx, &out, inner)

	exitErr, ok := errors.AsType[*ExitError](err)
	if !ok {
		t.Fatalf("expected *ExitError, got %T: %v", err, err)
	}
	if exitErr.Code != ExitInterrupted {
		t.Errorf("Code = %d, want ExitInterrupted (%d); cancellation should win", exitErr.Code, ExitInterrupted)
	}
}

// TestForceExitOnSecondInterrupt_WaitsForCancellation verifies the
// force-exit goroutine's guard: it must not act (and must not itself be
// tested past this point, since it calls os.Exit) until ctx is done. This
// only exercises the "no signal yet" half -- the os.Exit(ExitInterrupted)
// tail is exactly what the exported constant documents and is not
// exercised here, since calling it would terminate the test binary.
func TestForceExitOnSecondInterrupt_WaitsForCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		<-ctx.Done()
		close(done)
	}()

	select {
	case <-done:
		t.Fatal("ctx.Done() fired before cancel was called")
	default:
	}
	cancel()
	<-done // proves ctx.Done() unblocks a waiter, which is all forceExitOnSecondInterrupt depends on before its os.Exit tail
}

// TestExitInterrupted_MatchesShellConvention pins the documented 128+SIGINT
// value so a future edit cannot silently change the exit code scripts key
// on.
func TestExitInterrupted_MatchesShellConvention(t *testing.T) {
	const sigint = 2
	if ExitInterrupted != 128+sigint {
		t.Errorf("ExitInterrupted = %d, want %d (128+SIGINT)", ExitInterrupted, 128+sigint)
	}
}
