package compose

import (
	"fmt"
	"strings"
	"testing"
)

// TestNATSConfigContentRendersExpected pins the rendered NATS server config
// to its exact canonical bytes. The content is built with fmt.Sprintf from the
// NATSClientPort / NATSHTTPPort / NATSMaxPayload constants, so this guards
// against a constant typo or a template-format change silently altering what
// the NATS broker is configured with.
func TestNATSConfigContentRendersExpected(t *testing.T) {
	want := "host: 0.0.0.0\n" +
		"port: 4222\n" +
		"http_port: 8222\n" +
		"jetstream {\n" +
		"  store_dir: /data\n" +
		"}\n" +
		"max_payload: 16MB\n"
	if NATSConfig() != want {
		t.Errorf("NATSConfig() mismatch\n got:\n%q\nwant:\n%q", NATSConfig(), want)
	}
}

// TestNATSConfigContentUsesConstants asserts the rendered config carries the
// values from the named constants, so the single-source-of-truth wiring holds
// even if the surrounding template text is reworded.
func TestNATSConfigContentUsesConstants(t *testing.T) {
	for _, want := range []string{
		fmt.Sprintf("port: %d", NATSClientPort),
		fmt.Sprintf("http_port: %d", NATSHTTPPort),
		"max_payload: " + NATSMaxPayload,
	} {
		if !strings.Contains(NATSConfig(), want) {
			t.Errorf("NATSConfig() missing %q", want)
		}
	}
}
