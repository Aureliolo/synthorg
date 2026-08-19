package cmd

import (
	"context"
	"regexp"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// bootFailureTailLines is how far back a crash-looping container is read.
// A boot that aborts does so within a few dozen lines of starting, and each
// restart writes the same ones again, so a deeper tail buys nothing and
// costs the operator a slower status.
const bootFailureTailLines = "80"

// maxBootFailureLen bounds the quoted line: a structured log line carries
// the whole error, which can be a paragraph, and the banner has to stay a
// banner.
//
// This bounds the DATA, not the display width: a real migration refusal
// names the constraint AND the relation it was violated on, and cutting to
// a terminal width would drop the half that says what to fix. The banner
// keeps its shape by wrapping instead (see wrapBannerIssue).
const maxBootFailureLen = 240

// composeLogPrefix strips the "service-1  | " compose prepends to every
// line, which repeats the service the banner already names.
var composeLogPrefix = regexp.MustCompile(`^[a-zA-Z0-9_.-]+\s+\|\s?`)

// startupAbortedMarker is what an ASGI server prints once it has given up.
// It is the effect: it says a boot failed and never says why.
const startupAbortedMarker = "Application startup failed"

// bootFailureLine returns the line naming why a container's boot aborted,
// or "" when its log names none.
//
// A failed migration is the commonest upgrade failure there is, and a
// deployment that hit one reported "1 container(s) restarting" with a
// pointer to the logs, while the logs held the revision id and the
// constraint it violated. The cause is in the log either way; the only
// question is whether the operator has to go and find it.
//
// The line BEFORE the startup-aborted marker is the answer, not the marker
// itself: the marker is the effect and names nothing. Read newest-first so
// the most recent restart wins, since an earlier one may have failed on
// something since fixed.
func bootFailureLine(logs string) string {
	lines := cleanLogLines(logs)
	if aborted := lastIndexOf(lines, startupAbortedMarker); aborted >= 0 {
		if cause := lastFailureBefore(lines, aborted); cause != "" {
			return cause
		}
	}
	return lastFailureBefore(lines, len(lines))
}

// cleanLogLines makes each line safe to quote into a banner and strips the
// compose prefix, dropping the blanks.
//
// A backend log line is untrusted text: an agent's own output, a task title
// and an exception message all reach it, and the operator reads it here
// through a banner rather than through the pager `logs` gives raw. Stripping
// only the colour codes structlog writes would leave every other sequence
// (a cursor move, a screen clear, an OSC title set, a bare CR) free to
// rewrite what the operator is reading, so the whole line goes through the
// one sanitiser the CLI already owns for remote-sourced text. Its
// single-line form, because a banner row is a row: an embedded break would
// let a hostile line author a second one.
//
// Sanitising precedes the prefix strip: that pattern anchors at the start of
// the line, so a sequence sitting in front of "backend-1  | " would other-
// wise carry the prefix past it and into the banner.
func cleanLogLines(logs string) []string {
	var cleaned []string
	for raw := range strings.SplitSeq(logs, "\n") {
		line := strings.TrimSpace(ui.SanitizeUntrustedLine(raw))
		line = strings.TrimSpace(composeLogPrefix.ReplaceAllString(line, ""))
		if line != "" {
			cleaned = append(cleaned, line)
		}
	}
	return cleaned
}

// lastIndexOf returns the index of the last line containing marker, or -1.
func lastIndexOf(lines []string, marker string) int {
	for i := len(lines) - 1; i >= 0; i-- {
		if strings.Contains(lines[i], marker) {
			return i
		}
	}
	return -1
}

// lastFailureBefore returns the newest failure-shaped line above limit,
// bounded, or "".
func lastFailureBefore(lines []string, limit int) string {
	for i := limit - 1; i >= 0; i-- {
		if !namesAFailure(lines[i]) {
			continue
		}
		return truncateRunes(lines[i], maxBootFailureLen)
	}
	return ""
}

// levelMarker matches a structlog level marker, which is the padded level
// name inside brackets ("[error    ]") once the colours are stripped.
//
// Anchored to the whole bracketed token rather than matched as the bare
// substring "[error": a line whose MESSAGE quotes one reads as a cause
// otherwise, and the message is attacker-influenced text an agent can write.
var levelMarker = regexp.MustCompile(`(?i)\[(error|critical)\s*\]`)

// plainLevelPrefix matches the level format an ASGI server writes, which
// carries no brackets at all ("ERROR:    Application startup failed"). It is
// the shape of the very marker this file keys on, so a reader that only knew
// structlog's format could not name the cause of the commonest abort there is.
var plainLevelPrefix = regexp.MustCompile(`^(ERROR|CRITICAL):`)

// namesAFailure reports whether a line states a failure rather than
// counting one.
//
// The level marker is the test, not the word: a healthy backend logs
// "failed=0" on every reconciliation pass and every dispatcher start, and
// reading one of those as a cause would put a success in a CRITICAL banner.
func namesAFailure(line string) bool {
	return levelMarker.MatchString(line) ||
		plainLevelPrefix.MatchString(line) ||
		strings.HasPrefix(line, "Traceback (most recent call last)")
}

// truncateRunes bounds a line to limit runes INCLUDING the elision marker,
// so the bound means the width the banner gets. Runes rather than bytes, so
// a multi-byte character is never cut in half.
func truncateRunes(line string, limit int) string {
	const elision = "..."
	runes := []rune(line)
	if len(runes) <= limit {
		return line
	}
	// A limit too small to hold the marker would slice below zero. Cutting
	// without the marker is the honest answer at that width; panicking in
	// the middle of reporting somebody else's failure is not.
	if limit <= len(elision) {
		return string(runes[:max(limit, 0)])
	}
	return string(runes[:limit-len(elision)]) + elision
}

// gatherBootFailures reads the tail of every named service and returns the
// line each one aborted on, keyed by service.
//
// Only called for services already known to be restarting or unhealthy, so
// a healthy stack pays nothing for it. Best-effort throughout: a service
// whose log cannot be read simply contributes nothing, because the banner
// it feeds is already reporting the failure and must not be blocked by a
// second one.
// readServiceTail reads one service's recent log, bounded.
//
// Every other Docker call the status command makes is bounded by
// StatusDockerTimeout so an unresponsive daemon cannot hang it. This one is
// reached precisely when the stack is already failing, which is when the
// socket is most likely to be slow, so it needs the bound more than they do
// and not less.
func readServiceTail(
	ctx context.Context,
	info docker.Info,
	safeDir string,
	service string,
	timeout time.Duration,
) (string, error) {
	logCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	return docker.ComposeExecOutput(
		logCtx, info, safeDir,
		"logs", "--tail", bootFailureTailLines, "--no-log-prefix", service,
	)
}

func gatherBootFailures(
	ctx context.Context,
	info docker.Info,
	safeDir string,
	services []string,
) map[string]string {
	failures := make(map[string]string, len(services))
	timeout := GetGlobalOpts(ctx).Tunables.StatusDockerTimeout
	for _, service := range services {
		out, err := readServiceTail(ctx, info, safeDir, service, timeout)
		if err != nil {
			continue
		}
		if cause := bootFailureLine(out); cause != "" {
			failures[service] = cause
		}
	}
	return failures
}
