// Package health provides health check polling for the SynthOrg backend.
package health

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"
)

// healthResponse mirrors the backend API response envelope.
type healthResponse struct {
	Data struct {
		Status string `json:"status"`
	} `json:"data"`
}

// RateLimitedError reports an HTTP 429 from the health endpoint. The
// backend is alive but throttling, so callers should back off rather than
// treat the probe as a hard failure. RetryAfter carries the server's
// Retry-After hint (zero when absent or unparseable).
type RateLimitedError struct {
	RetryAfter time.Duration
}

func (e *RateLimitedError) Error() string {
	if e.RetryAfter > 0 {
		return fmt.Sprintf("health endpoint rate-limited; retry after %s", e.RetryAfter)
	}
	return "health endpoint rate-limited"
}

// parseRetryAfterSeconds reads a Retry-After header into a duration,
// honouring both forms RFC 9110 10.2.3 permits: an integer delta-seconds
// and an HTTP-date. A past HTTP-date, a negative delta, or a malformed
// value yields zero so the caller falls back to its own cadence.
func parseRetryAfterSeconds(header string) time.Duration {
	if header == "" {
		return 0
	}
	if secs, err := strconv.Atoi(header); err == nil {
		if secs < 0 {
			return 0
		}
		return time.Duration(secs) * time.Second
	}
	if when, err := http.ParseTime(header); err == nil {
		if delay := time.Until(when); delay > 0 {
			return delay
		}
	}
	return 0
}

// honourRetryAfter waits for a 429 Retry-After hint carried by err,
// bounded by ctx. It returns nil when there is no hint or the wait
// completes, and ctx.Err() if the deadline elapses during the wait so
// the caller can stop polling.
func honourRetryAfter(ctx context.Context, err error) error {
	var rle *RateLimitedError
	if !errors.As(err, &rle) || rle.RetryAfter <= 0 {
		return nil
	}
	select {
	case <-time.After(rle.RetryAfter):
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// WaitForHealthy polls the health endpoint until it returns status "ok" or the
// context is cancelled.
func WaitForHealthy(ctx context.Context, url string, timeout, interval, initialDelay time.Duration) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	// Wait for initial delay (container startup).
	select {
	case <-time.After(initialDelay):
	case <-ctx.Done():
		return ctx.Err()
	}

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	var lastErr error
	for {
		select {
		case <-ctx.Done():
			if lastErr != nil {
				return fmt.Errorf("health check timed out (last error: %w)", lastErr)
			}
			return fmt.Errorf("health check timed out")
		case <-ticker.C:
			if err := checkOnce(ctx, url); err != nil {
				lastErr = err
				if waitErr := honourRetryAfter(ctx, err); waitErr != nil {
					return fmt.Errorf("health check timed out (last error: %w)", lastErr)
				}
				continue
			}
			return nil
		}
	}
}

// healthClient is used for individual health check requests with a timeout.
// Set by Configure; defaults to 5s. Also exposed via HTTPClient() so other
// packages (cmd/status.go, diagnostics) reuse the same configured client
// instead of instantiating their own 5-second clients.
var healthClient = &http.Client{Timeout: 5 * time.Second}

// Configure applies the resolved health check timeout. Called exactly
// once from root.go PersistentPreRunE.
//
// The assignment is unconditional so Configure is deterministic across
// repeated calls (tests reset by passing the default from
// config.DefaultTunables().HealthCheckTimeout). A zero timeout here is a
// programmer error, not a no-op request -- validation belongs in
// config.ResolveTunables which refuses non-positive durations.
func Configure(timeout time.Duration) {
	healthClient = &http.Client{Timeout: timeout}
}

// HTTPClient returns the shared HTTP client configured for health and
// lightweight diagnostic requests. The client has a bounded timeout; do
// not reuse it for long-running downloads.
func HTTPClient() *http.Client { return healthClient }

func checkOnce(ctx context.Context, url string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}

	resp, err := healthClient.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
	if err != nil {
		return err
	}

	if resp.StatusCode == http.StatusTooManyRequests {
		return &RateLimitedError{
			RetryAfter: parseRetryAfterSeconds(resp.Header.Get("Retry-After")),
		}
	}

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("health endpoint returned %d", resp.StatusCode)
	}

	var hr healthResponse
	if err := json.Unmarshal(body, &hr); err != nil {
		return fmt.Errorf("invalid health response: %w", err)
	}

	if hr.Data.Status != "ok" {
		return fmt.Errorf("unhealthy: status=%q", hr.Data.Status)
	}

	return nil
}
