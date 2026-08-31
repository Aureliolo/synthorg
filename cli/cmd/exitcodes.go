package cmd

import (
	"errors"
	"fmt"
)

// Exit codes for the SynthOrg CLI.
const (
	ExitSuccess     = 0  // Successful execution.
	ExitRuntime     = 1  // Runtime error (something went wrong).
	ExitUsage       = 2  // Usage error (bad arguments, missing flags).
	ExitUnhealthy   = 3  // Backend or containers are unhealthy.
	ExitUnreachable = 4  // Docker not available or containers not running.
	ExitUpdateAvail = 10 // Updates available (used by --check).
	// ExitInterrupted follows the shell convention (128+SIGINT) so scripts
	// that check `$?` can tell a Ctrl+C apart from a genuine failure.
	ExitInterrupted = 130
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
