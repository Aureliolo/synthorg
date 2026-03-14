// Package health provides health check polling for the SynthOrg backend.
package health

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// healthResponse mirrors the backend API response envelope.
type healthResponse struct {
	Data struct {
		Status string `json:"status"`
	} `json:"data"`
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
				continue
			}
			return nil
		}
	}
}

// healthClient is used for individual health check requests with a timeout.
var healthClient = &http.Client{Timeout: 5 * time.Second}

func checkOnce(ctx context.Context, url string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}

	resp, err := healthClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
	if err != nil {
		return err
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
