package selfupdate

import (
	"net/http"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestRetryAfterMessage(t *testing.T) {
	tests := []struct {
		name   string
		header string
		want   string
	}{
		{name: "empty", header: "", want: "try again later"},
		{name: "delta seconds", header: "120", want: "retry after 120 seconds"},
		{name: "zero seconds", header: "0", want: "retry after 0 seconds"},
		{name: "negative seconds", header: "-5", want: "try again later"},
		{name: "garbage", header: "soon", want: "try again later"},
		{name: "huge delta capped", header: "999999999", want: "retry after 86400 seconds"},
		{name: "at cap", header: "86400", want: "retry after 86400 seconds"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := retryAfterMessage(tt.header); got != tt.want {
				t.Errorf("retryAfterMessage(%q) = %q, want %q", tt.header, got, tt.want)
			}
		})
	}
}

func TestRetryAfterMessageHTTPDate(t *testing.T) {
	// A far-future HTTP-date exercises the capped branch: the delta is
	// clamped to the one-day display ceiling before formatting.
	farFuture := time.Now().UTC().Add(48 * time.Hour).Format(http.TimeFormat)
	if got := retryAfterMessage(farFuture); got != "retry after 86400 seconds" {
		t.Fatalf("far-future HTTP-date retryAfterMessage = %q, want %q", got, "retry after 86400 seconds")
	}

	future := time.Now().UTC().Add(time.Hour).Format(http.TimeFormat)
	got := retryAfterMessage(future)
	// HTTP-date has one-second precision and time.Until is re-read inside
	// retryAfterMessage, so the delta is ~3600 with a small scheduling
	// jitter band rather than an exact value. Assert the shape + a range.
	const prefix = "retry after "
	const suffix = " seconds"
	if !strings.HasPrefix(got, prefix) || !strings.HasSuffix(got, suffix) {
		t.Fatalf("future HTTP-date retryAfterMessage = %q, want %q...%q", got, prefix, suffix)
	}
	secs, err := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(got, prefix), suffix))
	if err != nil {
		t.Fatalf("could not parse seconds from %q: %v", got, err)
	}
	// Lower bound allows a 60-second scheduling window between the Now()
	// above and the time.Until re-read inside retryAfterMessage, so a
	// heavily loaded runner cannot flake the assertion.
	if secs < 3540 || secs > 3600 {
		t.Errorf("future HTTP-date delay = %d seconds, want ~3600 (3540-3600)", secs)
	}

	past := time.Now().UTC().Add(-time.Hour).Format(http.TimeFormat)
	if got := retryAfterMessage(past); got != "try again later" {
		t.Errorf("past HTTP-date retryAfterMessage = %q, want %q", got, "try again later")
	}
}
