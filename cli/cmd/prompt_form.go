package cmd

import (
	"context"
	"errors"

	"charm.land/huh/v2"
)

// errPromptDismissed reports that a person dismissed an interactive form
// instead of answering it.
//
// It exists because the sentinel huh raises cannot answer that on its own.
// huh collapses bubbletea's ErrInterrupted into the same ErrUserAborted it
// raises for a Ctrl+C keypress, and bubbletea installs its own
// signal.Notify(SIGINT, SIGTERM) listener that no context governs, so a
// `docker stop` and a keypress arrive as one indistinguishable error. Only
// the command's own context separates them: Execute derives it from
// signal.NotifyContext, so a signal cancels it and a keypress does not.
//
// Conflating the two costs the interrupt contract. A run killed mid-prompt
// that reads as a dismissal takes the caller's clean-stop path and exits 0,
// and reportExecuteError returns on its `err == nil` branch before reaching
// the ctx.Err() one that owns "Interrupted." and ExitInterrupted.
var errPromptDismissed = errors.New("prompt dismissed")

// runPromptForm runs form under ctx and classifies how it ended:
// errPromptDismissed when a person dismissed it, the context's own error
// when a signal ended it, and anything else huh reports unchanged.
//
// Passing ctx down is load-bearing beyond the classification: a form run
// under a cancelled context stops on its own rather than holding the
// terminal open after the operator has already asked the process to stop.
//
// The keys that reach it are huh's, not ours: its default keymap binds
// abort to ctrl+c alone, and a Confirm field declares no Esc binding, so
// Esc does nothing on the prompts this CLI renders.
func runPromptForm(ctx context.Context, form *huh.Form) error {
	return classifyPromptOutcome(ctx, form.RunWithContext(ctx))
}

// classifyPromptOutcome decides what a finished form's error means. It is
// split from runPromptForm because the decision is testable and running a
// form is not: huh needs a terminal.
//
// The context is consulted BEFORE the sentinel, so a signal that arrives
// while a prompt is on screen is reported as the interruption it is rather
// than as an answer nobody gave.
func classifyPromptOutcome(ctx context.Context, err error) error {
	if err == nil {
		return nil
	}
	if ctxErr := ctx.Err(); ctxErr != nil {
		return ctxErr
	}
	if errors.Is(err, huh.ErrUserAborted) {
		return errPromptDismissed
	}
	return err
}

// promptDismissed reports whether err carries a dismissal, at any wrapping
// depth.
func promptDismissed(err error) bool {
	return errors.Is(err, errPromptDismissed)
}
