package cmd

import (
	"strings"
	"sync"
	"testing"
)

// TestLineSplitterBoundsPartialLine guards the OOM fix: output that never
// emits a newline must not grow the internal buffer without limit.
func TestLineSplitterBoundsPartialLine(t *testing.T) {
	l := newLineSplitter(func(string) {})

	// One oversized newline-free write.
	big := strings.Repeat("a", maxPullLineBytes*4)
	if _, err := l.Write([]byte(big)); err != nil {
		t.Fatalf("Write: %v", err)
	}
	if len(l.buf) > maxPullLineBytes {
		t.Fatalf("buffer grew to %d, want <= %d", len(l.buf), maxPullLineBytes)
	}

	// Repeated newline-free writes stay bounded too.
	for range 100 {
		if _, err := l.Write([]byte(big)); err != nil {
			t.Fatalf("Write: %v", err)
		}
	}
	if len(l.buf) > maxPullLineBytes {
		t.Fatalf("buffer grew to %d across repeated writes, want <= %d", len(l.buf), maxPullLineBytes)
	}
}

// TestLineSplitterForwardsCompleteLines confirms newline-delimited lines are
// forwarded intact and length-capped.
func TestLineSplitterForwardsCompleteLines(t *testing.T) {
	var got []string
	l := newLineSplitter(func(s string) { got = append(got, s) })

	if _, err := l.Write([]byte("first\r\nsecond\n")); err != nil {
		t.Fatalf("Write: %v", err)
	}
	if len(got) != 2 || got[0] != "first" || got[1] != "second" {
		t.Fatalf("forwarded lines = %#v, want [first second]", got)
	}

	got = nil
	long := strings.Repeat("x", maxPullLineBytes*2)
	if _, err := l.Write([]byte(long + "\n")); err != nil {
		t.Fatalf("Write: %v", err)
	}
	if len(got) != 1 || len(got[0]) != maxPullLineBytes {
		t.Fatalf("long line not capped: got %d lines, first len %d, want 1 line of %d",
			len(got), len(got[0]), maxPullLineBytes)
	}
}

// TestLineSplitterConcurrentWritesSerialiseCallback drives the splitter from
// two goroutines (the stdout/stderr shape it documents) and asserts onLine is
// never entered re-entrantly. Run with -race to also catch buffer data races.
func TestLineSplitterConcurrentWritesSerialiseCallback(t *testing.T) {
	var (
		active  int
		maxSeen int
		mu      sync.Mutex
	)
	l := newLineSplitter(func(string) {
		mu.Lock()
		active++
		if active > maxSeen {
			maxSeen = active
		}
		mu.Unlock()
		mu.Lock()
		active--
		mu.Unlock()
	})

	var wg sync.WaitGroup
	for range 4 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range 200 {
				_, _ = l.Write([]byte(strings.Repeat("y", i%8) + "\n"))
			}
		}()
	}
	wg.Wait()
	l.flush()

	if maxSeen > 1 {
		t.Fatalf("onLine entered concurrently (max active = %d, want 1)", maxSeen)
	}
}
