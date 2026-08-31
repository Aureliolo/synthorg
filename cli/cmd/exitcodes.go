package cmd

import (
	"errors"
	"fmt"
)

// Exit codes for the SynthOrg CLI.
const (
	ExitSuccess     = 0   // Successful execution.
	ExitRuntime     = 1   // Runtime error (something went wrong).
	ExitUsage       = 2   // Usage error (bad arguments, missing flags).
	ExitUnhealthy   = 3   // Backend or containers are unhealthy.
	ExitUnreachable = 4   // Docker not available or containers not running.
	ExitUpdateAvail = 10  // Updates available (used by --check).
	ExitInterrupted = 130 // Interrupted (SIGINT/SIGTERM); shell convention 128+SIGINT.
)

// ExitError wraps an error with a specific exit code.
// main.go inspects this type to set the process exit code.
type ExitError struct {
	Code int
	Err  error
}

// Error returns the underlying error message.
func (e *ExitError) Error() string {
	if e.Err != nil {
		return e.Err.Error()
	}
	return ""
}

// Unwrap returns the underlying error for errors.Is/As chains.
func (e *ExitError) Unwrap() error {
	return e.Err
}

// NewExitError creates an ExitError with the given code and error.
// err may be nil for exit-code-only signals (e.g. ExitUpdateAvail).
func NewExitError(code int, err error) *ExitError {
	return &ExitError{Code: code, Err: err}
}

// ChildExitError carries the exit code from a re-exec'd child process.
// The program entrypoint inspects this via ChildExitCode to call os.Exit
// with the child's code instead of printing a generic error message.
type ChildExitError struct {
	Code int
}

func (e *ChildExitError) Error() string {
	return fmt.Sprintf("re-launched CLI exited with code %d", e.Code)
}

// ChildExitCode extracts the exit code from err if it is a ChildExitError.
// Returns (code, true) if found, (0, false) otherwise.
func ChildExitCode(err error) (int, bool) {
	ce, ok := errors.AsType[*ChildExitError](err)
	if !ok {
		return 0, false
	}
	return ce.Code, true
}

// ResolveExitCode maps a non-nil error from Execute to the process exit
// code main.go should use.
//
// *ExitError is checked before ChildExitCode. Execute's interrupted-context
// branch wraps a re-exec'd child's own *ChildExitError as its cause (via
// NewExitError(ExitInterrupted, err)) to preserve it for errors.Is, and
// ChildExitCode's Unwrap-walking lookup would otherwise find that inner,
// signal-killed child code first -- silently overriding the outer
// ExitError's own code (reporting exit 1 for a Ctrl+C that Execute already
// classified and reported as ExitInterrupted). A bare *ChildExitError (the
// ordinary re-exec-failure case, never wrapped in an ExitError) is
// untouched by this ordering: it is not an *ExitError, so the first check
// falls through to ChildExitCode exactly as before.
func ResolveExitCode(err error) int {
	if exitErr, ok := errors.AsType[*ExitError](err); ok {
		return exitErr.Code
	}
	if code, ok := ChildExitCode(err); ok {
		return code
	}
	return ExitRuntime
}
