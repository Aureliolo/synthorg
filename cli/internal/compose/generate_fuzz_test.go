package compose

import (
	"strings"
	"testing"
)

func FuzzYamlStr(f *testing.F) {
	// Seed corpus with interesting strings.
	f.Add("")
	f.Add("simple")
	f.Add("hello world")
	f.Add(`contains "quotes"`)
	f.Add("has\nnewline")
	f.Add("has\ttab")
	f.Add("has\rcarriage")
	f.Add("dollar$sign")
	f.Add("colon: value")
	f.Add("hash # comment")
	f.Add("{curly}")
	f.Add("[bracket]")
	f.Add("pipe|char")
	f.Add("greater>than")
	f.Add("ampersand&char")
	f.Add("asterisk*char")
	f.Add("exclaim!char")
	f.Add("percent%char")
	f.Add("at@char")
	f.Add("backtick`char")
	f.Add(`backslash\char`)
	f.Add("single'quote")
	f.Add("$$escaped")
	f.Add("multi\nline\nstring")
	f.Add("\x00null\x00byte")

	f.Fuzz(func(t *testing.T, s string) {
		result := yamlStr(s)

		// Result must never be empty.
		if result == "" {
			t.Fatal("yamlStr returned empty string")
		}

		// Result must always be a double-quoted YAML string.
		if !strings.HasPrefix(result, `"`) {
			t.Fatalf("yamlStr result %q does not start with double quote", result)
		}
		if !strings.HasSuffix(result, `"`) {
			t.Fatalf("yamlStr result %q does not end with double quote", result)
		}

		// Interior must not contain an unescaped double-quote that would
		// prematurely close the YAML scalar. A quote is unescaped if
		// preceded by an even number of backslashes (including zero).
		inner := result[1 : len(result)-1]
		for i := 0; i < len(inner); i++ {
			if inner[i] != '"' {
				continue
			}
			// Count consecutive backslashes before this quote.
			backslashes := 0
			for j := i - 1; j >= 0 && inner[j] == '\\'; j-- {
				backslashes++
			}
			if backslashes%2 == 0 {
				t.Fatalf("yamlStr result contains unescaped inner double-quote at pos %d: %q", i, result)
			}
		}
	})
}

func FuzzValidateParams(f *testing.F) {
	// Seed corpus: imageTag, backendPort, webPort, logLevel, jwtSecret, sandbox, dockerSock.
	f.Add("latest", 8000, 3000, "info", "", false, "")
	f.Add("v1.0.0", 9000, 4000, "debug", "secret", false, "")
	f.Add("v2.0.0-rc1", 8080, 3001, "warn", "jwt-secret", true, "/var/run/docker.sock")
	f.Add("latest", 1, 65535, "error", "", false, "")
	f.Add("", 0, 0, "invalid", "", false, "")
	f.Add("tag!@#", -1, 99999, "", "", true, "")
	f.Add("latest", 8000, 8000, "info", "", false, "")
	f.Add("latest", 8000, 3000, "info", "", true, `path"with"quotes`)

	f.Fuzz(func(t *testing.T, imageTag string, backendPort, webPort int, logLevel, jwtSecret string, sandbox bool, dockerSock string) {
		p := Params{
			CLIVersion:  "dev",
			ImageTag:    imageTag,
			BackendPort: backendPort,
			WebPort:     webPort,
			LogLevel:    logLevel,
			JWTSecret:   jwtSecret,
			Sandbox:     sandbox,
			DockerSock:  dockerSock,
		}

		// Must not panic — either returns nil or a non-nil error.
		err := validateParams(p)
		if err != nil {
			return
		}
		// If validation passed, known invariants must hold.
		if backendPort < 1 || backendPort > 65535 {
			t.Fatalf("validateParams accepted invalid backendPort %d", backendPort)
		}
		if webPort < 1 || webPort > 65535 {
			t.Fatalf("validateParams accepted invalid webPort %d", webPort)
		}
		if backendPort == webPort {
			t.Fatal("validateParams accepted equal ports")
		}
		if sandbox && dockerSock == "" {
			t.Fatal("validateParams accepted sandbox=true with empty dockerSock")
		}
	})
}

func FuzzGenerate(f *testing.F) {
	// Seed corpus with valid-ish parameters that pass validateParams.
	f.Add("latest", 8000, 3000, "info", "", false, "")
	f.Add("v1.0.0", 9000, 4000, "debug", "my-secret", false, "")
	f.Add("v2.0.0", 8080, 3001, "warn", "jwt-key", true, "/var/run/docker.sock")
	f.Add("v0.1.5", 1, 2, "error", "", false, "")
	f.Add("latest", 65534, 65535, "", "", false, "")

	f.Fuzz(func(t *testing.T, imageTag string, backendPort, webPort int, logLevel, jwtSecret string, sandbox bool, dockerSock string) {
		p := Params{
			CLIVersion:  "dev",
			ImageTag:    imageTag,
			BackendPort: backendPort,
			WebPort:     webPort,
			LogLevel:    logLevel,
			JWTSecret:   jwtSecret,
			Sandbox:     sandbox,
			DockerSock:  dockerSock,
		}

		out, err := Generate(p)
		if err != nil {
			// Validation errors are expected for invalid inputs.
			if out != nil {
				t.Fatal("Generate returned both output and error")
			}
			return
		}

		// If Generate succeeds, the output must be non-empty.
		if len(out) == 0 {
			t.Fatal("Generate returned nil/empty output without error")
		}
	})
}
