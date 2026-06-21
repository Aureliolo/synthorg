package docker

import (
	"testing"
	"unicode/utf8"
)

func TestParseDockerBytes(t *testing.T) {
	t.Parallel()

	cases := []struct {
		token string
		want  int64
	}{
		{"0B", 0},
		{"1B", 1},
		{"2.097MB", 2097000},
		{"44.04MB", 44040000},
		{"1.5GB", 1500000000},
		{"512kB", 512000},
		{"512KB", 512000},
		{"3.2TB", 3200000000000},
		{"garbage", 0},
		{"", 0},
		{"-5MB", 0},
	}
	for _, c := range cases {
		t.Run(c.token, func(t *testing.T) {
			t.Parallel()
			if got := parseDockerBytes(c.token); got != c.want {
				t.Errorf("parseDockerBytes(%q) = %d, want %d", c.token, got, c.want)
			}
		})
	}
}

func TestPullProgressDockerPullForm(t *testing.T) {
	t.Parallel()

	var p PullProgress
	// Plain `docker pull` non-TTY lines: no byte counter, only state lines.
	lines := []string{
		"3.18: Pulling from library/alpine", // header, ignored
		"44cf07d57ee4: Pulling fs layer",
		"431d356fe850: Pulling fs layer",
		"44cf07d57ee4: Download complete",
		"431d356fe850: Download complete",
		"44cf07d57ee4: Pull complete",
		"Digest: sha256:de0eb0",                          // ignored
		"Status: Downloaded newer image for alpine:3.18", // ignored
	}
	for _, l := range lines {
		p.Observe(l)
	}

	got := p.Render()
	// Two layers discovered, one fully pulled; no bytes available.
	want := "pulling 1/2 layers"
	if got != want {
		t.Errorf("Render() = %q, want %q", got, want)
	}
}

func TestPullProgressComposeForm(t *testing.T) {
	t.Parallel()

	var p PullProgress
	// `docker compose pull` non-TTY lines carry a cumulative byte counter.
	lines := []string{
		" Image python:3.13-slim Pulling ", // header, ignored
		" bae41854fae8 Pulling fs layer 0B",
		" 9970f4c20ff1 Pulling fs layer 0B",
		" bae41854fae8 Downloading 2.097MB",
		" 9970f4c20ff1 Downloading 1.293MB",
		" bae41854fae8 Downloading 4.194MB", // advances same layer
		" 9970f4c20ff1 Download complete 0B",
		" Image python:3.13-slim Pulled ", // footer, ignored
	}
	for _, l := range lines {
		p.Observe(l)
	}

	got := p.Render()
	// bae41854fae8 at 4.194MB + 9970f4c20ff1 at 1.293MB = 5.487 MB downloaded.
	want := "downloading 5.5 MB, 0/2 layers"
	if got != want {
		t.Errorf("Render() = %q, want %q", got, want)
	}
}

func TestPullProgressExtractingDoesNotClobberBytes(t *testing.T) {
	t.Parallel()

	var p PullProgress
	lines := []string{
		" 72c03230f136 Downloading 44.04MB",
		" 72c03230f136 Download complete 0B",
		" 72c03230f136 Extracting 1B", // must NOT reset the 44.04MB
		" 72c03230f136 Extracting 2B",
		" 72c03230f136 Pull complete 0B",
	}
	for _, l := range lines {
		p.Observe(l)
	}
	// One layer, complete; phase is no longer downloading so bytes are not
	// shown, but the layer is counted complete.
	got := p.Render()
	want := "pulling 1/1 layers"
	if got != want {
		t.Errorf("Render() = %q, want %q", got, want)
	}
}

func TestPullProgressEmptyAndGarbage(t *testing.T) {
	t.Parallel()

	var p PullProgress
	if got := p.Render(); got != "" {
		t.Errorf("empty Render() = %q, want empty", got)
	}
	for _, l := range []string{"", "   ", "totally unrelated log line", "Digest: x"} {
		if p.Observe(l) {
			t.Errorf("Observe(%q) reported a change for an unrecognised line", l)
		}
	}
	if got := p.Render(); got != "" {
		t.Errorf("Render() after garbage = %q, want empty", got)
	}
}

func TestPullProgressObserveChangeSignal(t *testing.T) {
	t.Parallel()

	var p PullProgress
	if !p.Observe(" abc123abc123 Downloading 1MB") {
		t.Error("first observe should report a change")
	}
	if p.Observe(" abc123abc123 Downloading 1MB") {
		t.Error("identical observe should report no change")
	}
	if !p.Observe(" abc123abc123 Downloading 2MB") {
		t.Error("byte advance should report a change")
	}
	if !p.Observe(" abc123abc123 Download complete 0B") {
		t.Error("state transition should report a change")
	}
}

// FuzzParseDockerBytes asserts the byte parser never panics on arbitrary
// input and never returns a negative count.
func FuzzParseDockerBytes(f *testing.F) {
	for _, seed := range []string{"0B", "2.097MB", "1.5GB", "512kB", "3.2TB", "garbage", "", "-5MB", "..B", "9e999GB"} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, token string) {
		if got := parseDockerBytes(token); got < 0 {
			t.Errorf("parseDockerBytes(%q) returned negative %d", token, got)
		}
	})
}

// FuzzParsePullLine feeds arbitrary lines through the accumulator and asserts
// Observe and Render never panic and Render stays valid UTF-8.
func FuzzParsePullLine(f *testing.F) {
	for _, seed := range []string{
		" bae41854fae8 Downloading 2.097MB",
		"44cf07d57ee4: Pull complete",
		" Image python:3.13-slim Pulling ",
		"Digest: sha256:abc",
		"",
		"random noise",
	} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, line string) {
		var p PullProgress
		p.Observe(line)
		if got := p.Render(); !utf8.ValidString(got) {
			t.Errorf("Render() returned invalid UTF-8 for input %q: %q", line, got)
		}
	})
}
