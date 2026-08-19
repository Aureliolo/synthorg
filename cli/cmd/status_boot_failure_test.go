package cmd

import "testing"

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
			name: "reads a traceback header as the failure",
			logs: "backend-1  | Traceback (most recent call last):\n" +
				"backend-1  |   File \"/app/main.py\", line 1\n",
			want: "Traceback (most recent call last):",
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
		string(make([]byte, 0, maxBootFailureLen))
	for range maxBootFailureLen {
		coloured += "x"
	}
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
