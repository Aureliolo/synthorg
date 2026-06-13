package selfupdate

import (
	"net/http"
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
	future := time.Now().UTC().Add(time.Hour).Format(http.TimeFormat)
	got := retryAfterMessage(future)
	// ~3600s minus execution delta; bound generously.
	if got != "retry after 3599 seconds" && got != "retry after 3600 seconds" {
		t.Errorf("future HTTP-date retryAfterMessage = %q, want ~3600 seconds", got)
	}

	past := time.Now().UTC().Add(-time.Hour).Format(http.TimeFormat)
	if got := retryAfterMessage(past); got != "try again later" {
		t.Errorf("past HTTP-date retryAfterMessage = %q, want %q", got, "try again later")
	}
}
