package selfupdate

import (
	"context"
	"errors"
	"io"
	"net/http"
	"time"
)

// apiMaxAttempts bounds transient-failure retries for lightweight GitHub
// metadata requests (release listings, checksums, sigstore bundle,
// tag/commit resolution). api.github.com sits behind an edge layer, so an
// isolated 5xx or network blip is common and should not fail the whole
// update; a permanent 4xx or a rate-limit is not retried.
const apiMaxAttempts = 3

// apiRetryBaseDelay is the first backoff wait and doubles each attempt. It
// is a var, not a const, so tests can shrink it (see updater_test.go
// TestMain) to keep the 5xx-path suites fast.
var apiRetryBaseDelay = 500 * time.Millisecond

// doWithRetry issues an idempotent GET via client, retrying transient
// failures (a transport error while ctx is still live, or an HTTP 5xx)
// with capped exponential backoff. A non-5xx response (2xx or 4xx) is
// returned immediately so the caller applies its own status
// classification: retrying a 403/429 throttle only deepens it, and a 404
// is permanent. On exhausted 5xx the final response is returned (its body
// still open for the caller to close); on exhausted transport error the
// last error is returned. Every superseded response body is drained and
// closed so its keep-alive connection can be reused across attempts.
//
// The whole sequence shares one deadline derived from client.Timeout (a
// per-request ceiling that would otherwise let a stalling endpoint stretch
// the total wait to apiMaxAttempts * timeout). newReq builds a fresh
// request per attempt; callers pass GETs with no body, so re-issuing is
// safe.
func doWithRetry(
	ctx context.Context,
	client *http.Client,
	newReq func() (*http.Request, error),
) (*http.Response, error) {
	var lastResp *http.Response
	var lastErr error
	delay := apiRetryBaseDelay

	for attempt := 1; attempt <= apiMaxAttempts; attempt++ {
		resp, retry, err := doAttempt(ctx, client, newReq)
		if !retry {
			drainAndClose(lastResp)
			return resp, err
		}
		drainAndClose(lastResp)
		lastResp, lastErr = resp, err

		if attempt == apiMaxAttempts {
			break
		}
		if werr := waitBackoff(ctx, delay); werr != nil {
			drainAndClose(lastResp)
			return nil, werr
		}
		delay *= 2
	}

	if lastResp != nil {
		return lastResp, nil
	}
	return nil, lastErr
}

// doAttempt performs one request and classifies the outcome for
// doWithRetry. retry is true only for transient failures (a transport
// error while ctx is still live, or an HTTP 5xx); on a transient 5xx the
// response is returned so the final one can be handed back to the caller.
// A build error, a cancelled context, a CheckRedirect policy rejection, or
// any non-5xx response is terminal (retry=false) and returned as-is for the
// caller to classify.
func doAttempt(
	ctx context.Context,
	client *http.Client,
	newReq func() (*http.Request, error),
) (resp *http.Response, retry bool, err error) {
	req, err := newReq()
	if err != nil {
		return nil, false, err
	}
	resp, err = client.Do(req)
	if err != nil {
		// A cancelled context or a redirect-policy rejection is permanent,
		// not a transient blip; retrying only burns budget on the same
		// deterministic failure.
		if ctx.Err() != nil || errors.Is(err, errDisallowedRedirect) {
			return nil, false, err
		}
		return nil, true, err
	}
	if resp.StatusCode >= 500 {
		return resp, true, nil
	}
	return resp, false, nil
}

// waitBackoff sleeps for delay, returning ctx.Err() if the context is
// cancelled first so the retry loop stops promptly.
func waitBackoff(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// drainAndClose drains and closes a superseded response body so net/http
// can return its connection to the keep-alive pool, tolerating a nil
// response for call-site convenience.
func drainAndClose(resp *http.Response) {
	if resp == nil || resp.Body == nil {
		return
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
}
