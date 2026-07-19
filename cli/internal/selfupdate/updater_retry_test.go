package selfupdate

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// trackingBody records whether Close was called, to prove doWithRetry
// releases superseded response bodies.
type trackingBody struct {
	io.Reader
	closed *atomic.Bool
}

func (b *trackingBody) Close() error {
	b.closed.Store(true)
	return nil
}

// scriptedRoundTripper returns a canned status per attempt with a
// close-tracking body, so a test can assert which bodies were released
// without a real network. The last status repeats once exhausted.
type scriptedRoundTripper struct {
	statuses []int
	closed   []*atomic.Bool
	idx      int
}

func (rt *scriptedRoundTripper) RoundTrip(req *http.Request) (*http.Response, error) {
	status := rt.statuses[len(rt.statuses)-1]
	if rt.idx < len(rt.statuses) {
		status = rt.statuses[rt.idx]
	}
	rt.idx++
	c := &atomic.Bool{}
	rt.closed = append(rt.closed, c)
	return &http.Response{
		StatusCode: status,
		Body:       &trackingBody{Reader: strings.NewReader("body"), closed: c},
		Header:     make(http.Header),
		Request:    req,
	}, nil
}

// getWithRetry is a tiny test helper that drives doWithRetry with a plain
// GET against url and returns the resolved status (or -1 on error).
func getWithRetry(ctx context.Context, url string) (int, error) {
	resp, err := doWithRetry(ctx, &http.Client{}, func() (*http.Request, error) {
		return http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	})
	if err != nil {
		return -1, err
	}
	defer func() { _ = resp.Body.Close() }()
	return resp.StatusCode, nil
}

func TestDoWithRetry(t *testing.T) {
	t.Run("5xx then 200 recovers", func(t *testing.T) {
		var hits atomic.Int32
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			if hits.Add(1) == 1 {
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
			w.WriteHeader(http.StatusOK)
		}))
		defer srv.Close()

		status, err := getWithRetry(context.Background(), srv.URL)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if status != http.StatusOK {
			t.Errorf("status = %d, want 200", status)
		}
		if got := hits.Load(); got != 2 {
			t.Errorf("handler hit %d times, want 2 (one 503 + one 200)", got)
		}
	})

	t.Run("superseded 5xx body is drained and closed", func(t *testing.T) {
		rt := &scriptedRoundTripper{statuses: []int{http.StatusServiceUnavailable, http.StatusOK}}
		client := &http.Client{Transport: rt}
		resp, err := doWithRetry(context.Background(), client, func() (*http.Request, error) {
			return http.NewRequestWithContext(context.Background(), http.MethodGet, "http://example.invalid/", nil)
		})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		defer func() { _ = resp.Body.Close() }()
		if resp.StatusCode != http.StatusOK {
			t.Errorf("status = %d, want 200", resp.StatusCode)
		}
		if !rt.closed[0].Load() {
			t.Error("superseded 503 body was not closed (connection leak on the recovery path)")
		}
		if rt.closed[1].Load() {
			t.Error("returned 200 body was closed before the caller could read it")
		}
	})

	t.Run("exhausts 5xx and returns last response", func(t *testing.T) {
		var hits atomic.Int32
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			hits.Add(1)
			w.WriteHeader(http.StatusServiceUnavailable)
		}))
		defer srv.Close()

		status, err := getWithRetry(context.Background(), srv.URL)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if status != http.StatusServiceUnavailable {
			t.Errorf("status = %d, want 503", status)
		}
		if got := hits.Load(); int(got) != apiMaxAttempts {
			t.Errorf("handler hit %d times, want apiMaxAttempts=%d", got, apiMaxAttempts)
		}
	})

	t.Run("429 is not retried", func(t *testing.T) {
		var hits atomic.Int32
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			hits.Add(1)
			w.WriteHeader(http.StatusTooManyRequests)
		}))
		defer srv.Close()

		status, err := getWithRetry(context.Background(), srv.URL)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if status != http.StatusTooManyRequests {
			t.Errorf("status = %d, want 429", status)
		}
		if got := hits.Load(); got != 1 {
			t.Errorf("handler hit %d times, want 1 (rate-limit must not retry)", got)
		}
	})

	t.Run("disallowed redirect is terminal (not retried)", func(t *testing.T) {
		var hits atomic.Int32
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			hits.Add(1)
			http.Redirect(w, r, "https://disallowed.example.invalid/", http.StatusFound)
		}))
		defer srv.Close()

		client := &http.Client{CheckRedirect: checkRedirectHost}
		resp, err := doWithRetry(context.Background(), client, func() (*http.Request, error) {
			return http.NewRequestWithContext(context.Background(), http.MethodGet, srv.URL, nil)
		})
		if resp != nil {
			_ = resp.Body.Close()
		}
		if err == nil {
			t.Fatal("expected a disallowed-redirect error")
		}
		if !errors.Is(err, errDisallowedRedirect) {
			t.Errorf("error = %v, want errDisallowedRedirect", err)
		}
		if got := hits.Load(); got != 1 {
			t.Errorf("handler hit %d times, want 1 (redirect-policy rejection must not retry)", got)
		}
	})

	t.Run("404 is not retried", func(t *testing.T) {
		var hits atomic.Int32
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			hits.Add(1)
			w.WriteHeader(http.StatusNotFound)
		}))
		defer srv.Close()

		status, err := getWithRetry(context.Background(), srv.URL)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if status != http.StatusNotFound {
			t.Errorf("status = %d, want 404", status)
		}
		if got := hits.Load(); got != 1 {
			t.Errorf("handler hit %d times, want 1 (permanent 4xx must not retry)", got)
		}
	})

	t.Run("transport error is retried to the cap", func(t *testing.T) {
		// A never-listening address: every attempt fails at the transport
		// layer while ctx stays live, so all attempts are consumed.
		start := time.Now()
		_, err := getWithRetry(context.Background(), "http://127.0.0.1:0/never")
		if err == nil {
			t.Fatal("expected a transport error")
		}
		// apiRetryBaseDelay is 1ms in tests; two backoffs is trivially
		// fast, this only guards against an accidental multi-second wait.
		if elapsed := time.Since(start); elapsed > 5*time.Second {
			t.Errorf("retry loop took %s, expected sub-second with test backoff", elapsed)
		}
	})

	t.Run("context cancelled during backoff stops promptly", func(t *testing.T) {
		// Force a long backoff so the select blocks on time.After, then
		// cancel once the first attempt has landed: the ctx.Done() branch
		// must win deterministically (no reliance on wall-clock timing).
		prev := apiRetryBaseDelay
		apiRetryBaseDelay = time.Hour
		t.Cleanup(func() { apiRetryBaseDelay = prev })

		firstHit := make(chan struct{}, 1)
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			select {
			case firstHit <- struct{}{}:
			default:
			}
			w.WriteHeader(http.StatusServiceUnavailable)
		}))
		defer srv.Close()

		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()

		type result struct {
			err error
		}
		done := make(chan result, 1)
		go func() {
			_, err := getWithRetry(ctx, srv.URL)
			done <- result{err: err}
		}()

		<-firstHit // first attempt returned 503; loop is now in backoff
		cancel()

		select {
		case r := <-done:
			if r.err == nil {
				t.Fatal("expected a context cancellation error")
			}
			if !strings.Contains(r.err.Error(), "context canceled") {
				t.Errorf("error = %v, want context canceled", r.err)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("doWithRetry did not return after context cancellation")
		}
	})
}

// TestFetchJSON_retriesOnTransient5xx verifies the real fetchJSON path
// (through the shared apiClient) recovers from a transient 503 before
// decoding the body.
func TestFetchJSON_retriesOnTransient5xx(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if hits.Add(1) == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		if err := json.NewEncoder(w).Encode(Release{TagName: "v9.9.9"}); err != nil {
			t.Logf("encode error: %v", err)
		}
	}))
	defer srv.Close()

	rel, err := fetchJSON[Release](context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("fetchJSON: %v", err)
	}
	if rel.TagName != "v9.9.9" {
		t.Errorf("tag = %q, want v9.9.9", rel.TagName)
	}
	if got := hits.Load(); got != 2 {
		t.Errorf("handler hit %d times, want 2 (one 503 + one 200)", got)
	}
}
