package health

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestParseRetryAfterSeconds(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name   string
		header string
		want   time.Duration
	}{
		{name: "empty", header: "", want: 0},
		{name: "delta_seconds", header: "30", want: 30 * time.Second},
		{name: "zero", header: "0", want: 0},
		{name: "negative", header: "-5", want: 0},
		{name: "garbage", header: "soon", want: 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if got := parseRetryAfterSeconds(tc.header); got != tc.want {
				t.Fatalf("parseRetryAfterSeconds(%q) = %s, want %s", tc.header, got, tc.want)
			}
		})
	}
}

func TestParseRetryAfterSecondsHTTPDate(t *testing.T) {
	t.Parallel()
	future := time.Now().Add(time.Hour).UTC().Format(http.TimeFormat)
	if got := parseRetryAfterSeconds(future); got <= 0 || got > time.Hour+time.Minute {
		t.Fatalf("future HTTP-date parsed to %s, want a positive sub-hour delay", got)
	}
	past := time.Now().Add(-time.Hour).UTC().Format(http.TimeFormat)
	if got := parseRetryAfterSeconds(past); got != 0 {
		t.Fatalf("past HTTP-date parsed to %s, want 0", got)
	}
}

func TestCheckOnce429ReturnsRateLimitedError(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "12")
		w.WriteHeader(http.StatusTooManyRequests)
	}))
	defer srv.Close()

	err := checkOnce(context.Background(), srv.URL)
	var rle *RateLimitedError
	if !errors.As(err, &rle) {
		t.Fatalf("expected *RateLimitedError, got %v", err)
	}
	if rle.RetryAfter != 12*time.Second {
		t.Fatalf("RetryAfter = %s, want 12s", rle.RetryAfter)
	}
}

func TestCheckOnce429NoHeader(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
	}))
	defer srv.Close()

	err := checkOnce(context.Background(), srv.URL)
	var rle *RateLimitedError
	if !errors.As(err, &rle) {
		t.Fatalf("expected *RateLimitedError, got %v", err)
	}
	if rle.RetryAfter != 0 {
		t.Fatalf("RetryAfter = %s, want 0", rle.RetryAfter)
	}
}

func TestWaitForHealthySuccess(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"status":"ok"}}`))
	}))
	defer srv.Close()

	err := WaitForHealthy(context.Background(), srv.URL, 5*time.Second, 100*time.Millisecond, 0)
	if err != nil {
		t.Fatalf("expected healthy, got: %v", err)
	}
}

func TestWaitForHealthyDegraded(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"status":"degraded"}}`))
	}))
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	err := WaitForHealthy(ctx, srv.URL, 500*time.Millisecond, 100*time.Millisecond, 0)
	if err == nil {
		t.Fatal("expected error for degraded status")
	}
}

func TestWaitForHealthyTimeout(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()

	err := WaitForHealthy(context.Background(), srv.URL, 300*time.Millisecond, 50*time.Millisecond, 0)
	if err == nil {
		t.Fatal("expected timeout error")
	}
}

func TestWaitForHealthyEventualSuccess(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls < 3 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"status":"ok"}}`))
	}))
	defer srv.Close()

	err := WaitForHealthy(context.Background(), srv.URL, 5*time.Second, 100*time.Millisecond, 0)
	if err != nil {
		t.Fatalf("expected eventual success, got: %v", err)
	}
}

func TestWaitForHealthyWithInitialDelay(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"status":"ok"}}`))
	}))
	defer srv.Close()

	start := time.Now()
	err := WaitForHealthy(context.Background(), srv.URL, 5*time.Second, 100*time.Millisecond, 200*time.Millisecond)
	elapsed := time.Since(start)

	if err != nil {
		t.Fatalf("expected healthy, got: %v", err)
	}
	if elapsed < 200*time.Millisecond {
		t.Errorf("initial delay not respected: elapsed %v", elapsed)
	}
}

func TestWaitForHealthyCancelledDuringDelay(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"status":"ok"}}`))
	}))
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	// Initial delay is longer than context timeout.
	err := WaitForHealthy(ctx, srv.URL, 5*time.Second, 100*time.Millisecond, 1*time.Second)
	if err == nil {
		t.Fatal("expected context cancellation error")
	}
}

func TestCheckOnceSuccess(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"status":"ok"}}`))
	}))
	defer srv.Close()

	if err := checkOnce(context.Background(), srv.URL); err != nil {
		t.Fatalf("checkOnce: %v", err)
	}
}

func TestCheckOnceUnhealthy(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"status":"down"}}`))
	}))
	defer srv.Close()

	err := checkOnce(context.Background(), srv.URL)
	if err == nil {
		t.Fatal("expected error for unhealthy status")
	}
}

func TestCheckOnceInvalidJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("not json"))
	}))
	defer srv.Close()

	err := checkOnce(context.Background(), srv.URL)
	if err == nil {
		t.Fatal("expected error for invalid JSON")
	}
}

func TestCheckOnceHTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	err := checkOnce(context.Background(), srv.URL)
	if err == nil {
		t.Fatal("expected error for 500")
	}
}

func TestWaitForHealthyTimeoutWithLastError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"data":{"status":"down"}}`))
	}))
	defer srv.Close()

	err := WaitForHealthy(context.Background(), srv.URL, 300*time.Millisecond, 50*time.Millisecond, 0)
	if err == nil {
		t.Fatal("expected error")
	}
	// Should include "last error" in the message.
	if !strings.Contains(err.Error(), "last error") {
		t.Errorf("error should mention last error: %v", err)
	}
}
