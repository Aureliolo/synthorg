package cmd

import (
	"strings"
	"testing"
)

// The log a wedged deployment actually produced: the migration refused, and
// the process reported only that startup had failed.
const wedgedBackendLog = `
backend-1  | 2026-08-18 19:02:11 [info     ] persistence.migration.started  backend=postgres operation=apply
backend-1  | 2026-08-18 19:02:11 [info     ] Applying 20260818000000_blocked_reason_no_capable_agent
backend-1  | 2026-08-18 19:02:11 [error    ] persistence.migration.failed   error='CheckViolation: check constraint "tasks_blocked_reason_check" of relation "tasks" is violated by some row' error_type=CheckViolation operation=apply
backend-1  | ERROR:    Application startup failed. Exiting.
`

func TestBootFailureLine(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		logs string
		want string
	}{
		{
			// The whole point of the finding: 'status' reported "1 container
			// restarting" and pointed at the logs, and the logs said which
			// revision refused and why.
			name: "names the cause behind an aborted startup",
			logs: wedgedBackendLog,
			want: `2026-08-18 19:02:11 [error    ] persistence.migration.failed   error='CheckViolation: check constraint "tasks_blocked_reason_check" of relation "tasks" is violated by some row' error_type=CheckViolation operation=apply`,
		},
		{
			// The effect, not the cause. Returning it would replace one
			// uninformative line with another.
			name: "prefers the cause over the startup-failed marker",
			logs: "backend-1  | [error    ] db.connect.failed error='refused'\n" +
				"backend-1  | ERROR:    Application startup failed. Exiting.\n",
			want: "[error    ] db.connect.failed error='refused'",
		},
		{
			name: "falls back to the last error when startup never reported",
			logs: "backend-1  | [info     ] api.app.startup  service=ok\n" +
				"backend-1  | [critical ] worker.pool.exhausted count=8\n",
			want: "[critical ] worker.pool.exhausted count=8",
		},
		{
			// A block cut off before its exception names no cause, so the
			// header is all there is to say.
			name: "reads a traceback header as the failure",
			logs: "backend-1  | Traceback (most recent call last):\n" +
				"backend-1  |   File \"/app/main.py\", line 1\n",
			want: "Traceback (most recent call last):",
		},
		{
			// The word "Traceback" is the one thing the operator already knew
			// from the restart. The cause is the block's last line, and it
			// carries no level marker, so a reader keying on markers alone
			// reports the header and names nothing.
			name: "names the exception a traceback ends on, not its header",
			logs: "backend-1  | ERROR:    Traceback (most recent call last):\n" +
				"backend-1  |   File \"/app/main.py\", line 12, in <module>\n" +
				"backend-1  |     apply_migrations()\n" +
				"backend-1  | psycopg.errors.CheckViolation: check constraint " +
				"\"tasks_blocked_reason_check\" is violated by some row\n" +
				"backend-1  | ERROR:    Application startup failed. Exiting.\n",
			want: "psycopg.errors.CheckViolation: check constraint " +
				"\"tasks_blocked_reason_check\" is violated by some row",
		},
		{
			// The scan forward must stop at the next record, or a traceback
			// followed by unrelated chatter reports the chatter as the cause.
			name: "a traceback cause never runs into the next record",
			logs: "backend-1  | Traceback (most recent call last):\n" +
				"backend-1  |   File \"/app/main.py\", line 12, in <module>\n" +
				"backend-1  | ValueError: bad revision id\n" +
				"backend-1  | 2026-08-18 19:02:12 [info     ] api.shutdown.complete\n",
			want: "ValueError: bad revision id",
		},
		{
			// A healthy log is full of failure COUNTS. Reading one as a
			// cause would put "failed=0" in a CRITICAL banner.
			name: "a zero failure count is not a failure",
			logs: "backend-1  | [info     ] subsystem.reconcile.completed activated=32 failed=0\n" +
				"backend-1  | [info     ] notification.dispatcher.started failed=0 sinks=1\n",
			want: "",
		},
		{
			name: "empty logs name nothing",
			logs: "",
			want: "",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := bootFailureLine(tt.logs); got != tt.want {
				t.Errorf("bootFailureLine() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestBootFailureLineStripsAnsiAndBounds(t *testing.T) {
	t.Parallel()
	coloured := "backend-1  | \x1b[31m[error    ]\x1b[0m boot.failed reason='" +
		strings.Repeat("x", maxBootFailureLen)
	got := bootFailureLine(coloured)
	if len([]rune(got)) > maxBootFailureLen {
		t.Errorf("bootFailureLine() returned %d runes, want at most %d", len([]rune(got)), maxBootFailureLen)
	}
	if got == "" {
		t.Fatal("bootFailureLine() dropped a coloured error line")
	}
	if got[0] == '\x1b' {
		t.Error("bootFailureLine() kept an ANSI escape")
	}
}

// rtlOverride is RIGHT-TO-LEFT OVERRIDE, which reverses the rendering of
// everything after it and so lets a hostile line read as a different one.
// Written as its code point because the character itself is invisible in a
// source file, and a reviewer cannot check what they cannot see.
const rtlOverride = rune(0x202E)

// TestBootFailureLineCarriesNothingThatActsOnATerminal asserts the invariant,
// not one sequence: whatever a log line holds, what the banner quotes only
// prints.
//
// The line is untrusted (an agent's output and an exception message both
// reach the backend log), and a reader that stripped the colour codes
// structlog writes would pass every OTHER sequence through to the operator's
// terminal: a screen clear, a cursor move, an OSC title set, a bare CR that
// overwrites the row, a RIS that resets the terminal whole.
func TestBootFailureLineCarriesNothingThatActsOnATerminal(t *testing.T) {
	t.Parallel()
	hostile := []struct {
		name    string
		payload string
	}{
		{"screen clear", "\x1b[2J"},
		{"cursor home", "\x1b[H"},
		{"osc title set", "\x1b]0;owned\x07"},
		{"full terminal reset", "\x1bc"},
		{"carriage return overwrite", "\rall is well"},
		{"backspace erase", "boot ok\x08\x08"},
		{"bell", "\a"},
		{"right-to-left override", string(rtlOverride)},
	}
	for _, tt := range hostile {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			line := "backend-1  | [error    ] boot.failed reason='" + tt.payload + "'"
			got := bootFailureLine(line)
			if got == "" {
				t.Fatal("bootFailureLine() dropped the error line entirely")
			}
			for _, r := range got {
				if r == '\x1b' || r == '\r' || r == '\a' || r == '\x08' || r == rtlOverride {
					t.Errorf("bootFailureLine() = %q, which still acts on a terminal", got)
				}
			}
		})
	}
}

func TestNamesAFailure(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		line string
		want bool
	}{
		{"structlog error marker", "2026-01-01 [error    ] boot.failed", true},
		{"structlog critical marker", "2026-01-01 [critical ] boot.failed", true},
		{"traceback header", "Traceback (most recent call last)", true},
		// The format an ASGI server writes. It carries no brackets, and it
		// is the shape of the abort marker this file keys on, so missing it
		// means missing the cause of the commonest boot failure there is.
		{"plain uvicorn error", "ERROR:    [Errno 98] Address already in use", true},
		// A healthy line reporting zero failures, and a message that merely
		// QUOTES a marker. Both are attacker-influenceable text an agent
		// can write into a log, and reading either as a cause puts a
		// success, or somebody else's words, in a CRITICAL banner.
		{"a zero-failure count", "2026-01-01 [info     ] sweep.done failed=0", false},
		{"a message quoting a marker", "2026-01-01 [info     ] parsed text='[error' ok=1", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := namesAFailure(tt.line); got != tt.want {
				t.Errorf("namesAFailure(%q) = %v, want %v", tt.line, got, tt.want)
			}
		})
	}
}

func TestWrapBannerIssueKeepsTheBoxNarrowWithoutLosingText(t *testing.T) {
	t.Parallel()
	issue := "backend aborted on: " + strings.Repeat("word ", 60)

	lines := wrapBannerIssue(issue)

	if len(lines) < 2 {
		t.Fatalf("wrapBannerIssue() returned %d line(s), want it wrapped", len(lines))
	}
	for _, line := range lines {
		if len([]rune(line)) > bannerIssueWidth+4 {
			t.Errorf("line %q is %d runes, wider than the box allows", line, len([]rune(line)))
		}
	}
	// Wrapping must not be truncation: every word survives somewhere.
	rejoined := strings.Join(lines, " ")
	if !strings.Contains(rejoined, "backend aborted on:") {
		t.Error("wrapBannerIssue() lost the head of the issue")
	}
	if strings.Count(rejoined, "word") != 60 {
		t.Errorf("wrapBannerIssue() kept %d of 60 words", strings.Count(rejoined, "word"))
	}
}

func TestTruncateRunesNeverPanicsBelowTheElision(t *testing.T) {
	t.Parallel()
	// Unreachable from the one caller today, which passes a constant. The
	// next caller is the one this guards.
	for limit := range 5 {
		got := truncateRunes("a much longer line than the limit", limit)
		if len([]rune(got)) > limit {
			t.Errorf("truncateRunes(limit=%d) returned %d runes", limit, len([]rune(got)))
		}
	}
}
