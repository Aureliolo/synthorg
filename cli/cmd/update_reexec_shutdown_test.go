package cmd

import (
	"context"
	"os"
	"os/exec"
	"testing"
	"time"
)

// helperEnv marks the re-exec'd copy of this test binary as the child half of
// TestConfigureReexecShutdown_letsTheChildFinishUnwinding.
const helperEnv = "SYNTHORG_TEST_REEXEC_CHILD"

// helperRunMillis is how long the stand-in child "unwinds" for. It has to
// outlast the parent's cancellation by enough that a killed child and a
// waited-for child are told apart by the outcome rather than by timing luck.
const helperRunMillis = 400

// TestMain lets this package's test binary double as the child process the
// shutdown test needs. A real re-exec'd `synthorg update` cannot be driven
// from a unit test, but the thing under test is not the update: it is whether
// the parent kills a child that is still unwinding, and any child that
// outlives its parent's cancellation proves that either way.
func TestMain(m *testing.M) {
	switch os.Getenv(helperEnv) {
	case "1":
		time.Sleep(helperRunMillis * time.Millisecond)
		os.Exit(0)
	case "fail":
		os.Exit(1)
	}
	os.Exit(m.Run())
}

// failedChildError returns a real *exec.ExitError, the shape a subprocess
// failure genuinely takes on its way out of docker.ComposeExec (which returns
// cmd.Run() unwrapped). Tests that assert on how such an error is REPORTED
// have to use the real type: a hand-built errors.New carrying the same words
// is a different value to anything that inspects the chain.
func failedChildError(t *testing.T) error {
	t.Helper()
	c := exec.CommandContext(t.Context(), os.Args[0], "-test.run=TestMain") //nolint:gosec // G204: the test binary re-execing itself
	c.Env = append(os.Environ(), helperEnv+"=fail")
	err := c.Run()
	if err == nil {
		t.Fatal("helper child should have failed")
	}
	return err
}

// killedChildError returns the *exec.ExitError a child KILLED mid-run
// produces, which failedChildError deliberately does not: that one exits
// non-zero of its own accord, and the two are the same Go type carrying
// opposite meanings. Built by letting the stdlib's default cancel action
// fire, the same path a real `docker compose pull` takes when the operator's
// Ctrl+C cancels the command's context.
func killedChildError(t *testing.T) error {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	c := exec.CommandContext(ctx, os.Args[0], "-test.run=TestMain") //nolint:gosec // G204: the test binary re-execing itself
	c.Env = append(os.Environ(), helperEnv+"=1")
	if err := c.Start(); err != nil {
		t.Fatalf("starting helper child: %v", err)
	}
	// Well inside the child's run, as in the shutdown tests below, so the
	// kill lands while it still has work left rather than racing its exit.
	time.AfterFunc(helperRunMillis/4*time.Millisecond, cancel)

	err := c.Wait()
	if err == nil {
		t.Fatal("helper child should have been killed")
	}
	return err
}

// TestConfigureReexecShutdown_letsTheChildFinishUnwinding is the regression
// guard for the desync `synthorg update` exists to prevent.
//
// `update`'s default path re-execs the freshly installed binary and lets the
// child do the compose write, the pull and the rollback. Once Execute() runs
// under a signal-cancellable context, a plain exec.CommandContext child is
// SIGKILLed the instant the parent's context is done, which lands squarely
// between the compose write and the rollback: compose.yml holds the new pins
// and config.json holds the old state, permanently.
//
// Cancelling mid-run and requiring a clean exit is what separates the two
// behaviours: the default cancel action reports "signal: killed" here.
func TestConfigureReexecShutdown_letsTheChildFinishUnwinding(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	c := exec.CommandContext(ctx, os.Args[0], "-test.run=TestMain") //nolint:gosec // G204: the test binary re-execing itself
	c.Env = append(os.Environ(), helperEnv+"=1")
	configureReexecShutdown(c)

	if err := c.Start(); err != nil {
		t.Fatalf("starting helper child: %v", err)
	}
	// Well inside the child's run, so the cancellation genuinely lands while
	// it still has work left rather than racing its exit.
	time.AfterFunc(helperRunMillis/4*time.Millisecond, cancel)

	if err := c.Wait(); err != nil {
		t.Fatalf("child should have been allowed to finish, got %v", err)
	}
}

// TestConfigureReexecShutdown_defaultWouldKillTheChild proves the test above
// can fail. Without it, a change that quietly dropped configureReexecShutdown
// would still leave a green suite if the stdlib's default were harmless here.
// It is not, and this pins the difference the fix turns on.
func TestConfigureReexecShutdown_defaultWouldKillTheChild(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	c := exec.CommandContext(ctx, os.Args[0], "-test.run=TestMain") //nolint:gosec // G204: the test binary re-execing itself
	c.Env = append(os.Environ(), helperEnv+"=1")
	// Deliberately NOT configureReexecShutdown(c).

	if err := c.Start(); err != nil {
		t.Fatalf("starting helper child: %v", err)
	}
	time.AfterFunc(helperRunMillis/4*time.Millisecond, cancel)

	if err := c.Wait(); err == nil {
		t.Fatal("the default cancel action should have killed the child")
	}
}

// TestConfigureReexecShutdown_boundsTheWait pins the other half: patience is
// not unlimited. A SIGTERM aimed at the parent alone never reaches the child,
// so something has to end a child that was never told to stop.
func TestConfigureReexecShutdown_boundsTheWait(t *testing.T) {
	c := exec.CommandContext(t.Context(), os.Args[0]) //nolint:gosec // G204: the test binary, never run
	configureReexecShutdown(c)

	if c.WaitDelay <= 0 {
		t.Errorf("WaitDelay = %v, want a positive bound", c.WaitDelay)
	}
	if c.Cancel == nil {
		t.Fatal("Cancel should be set so the default kill does not apply")
	}
	if err := c.Cancel(); err != os.ErrProcessDone { //nolint:errorlint // identity is the contract
		t.Errorf("Cancel() = %v, want os.ErrProcessDone so Wait keeps the child's own verdict", err)
	}
}
