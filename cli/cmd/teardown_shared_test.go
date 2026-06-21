package cmd

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestIsNotInitialisedErr(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{"nil", nil, false},
		{"raw docker jargon", errors.New("exit status 1: no configuration file provided: not found"), true},
		{"wrapped docker jargon", fmt.Errorf("stopping containers: %w", errors.New("no configuration file provided: not found")), true},
		{"mixed case", errors.New("No Configuration File Provided"), true},
		{"unrelated error", errors.New("connection refused"), false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := isNotInitialisedErr(tt.err); got != tt.want {
				t.Errorf("isNotInitialisedErr(%v) = %v, want %v", tt.err, got, tt.want)
			}
		})
	}
}

func TestComposeFilePath(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	got, err := composeFilePath(dir)
	if err != nil {
		t.Fatalf("composeFilePath with no compose.yml: unexpected err %v", err)
	}
	if got != "" {
		t.Errorf("composeFilePath with no compose.yml = %q, want \"\"", got)
	}
	compose := filepath.Join(dir, "compose.yml")
	if err := os.WriteFile(compose, []byte("services: {}"), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err = composeFilePath(dir)
	if err != nil {
		t.Fatalf("composeFilePath: unexpected err %v", err)
	}
	if got != compose {
		t.Errorf("composeFilePath = %q, want %q", got, compose)
	}
}
