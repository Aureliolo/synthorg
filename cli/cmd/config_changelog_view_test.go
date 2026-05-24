package cmd

import (
	"bytes"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

func TestConfigSetChangelogView(t *testing.T) {
	tests := []struct {
		name      string
		setValue  string
		wantState string
		wantErr   bool
	}{
		{"highlights", "highlights", "highlights", false},
		{"commits", "commits", "commits", false},
		{"invalid_value", "raw", "", true},
		{"empty_value_rejected", "", "", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			state := config.DefaultState()
			state.EncryptSecrets = false
			state.DataDir = dir
			if err := config.Save(state); err != nil {
				t.Fatal(err)
			}

			var buf bytes.Buffer
			rootCmd.SetOut(&buf)
			rootCmd.SetErr(&buf)
			rootCmd.SetArgs([]string{"config", "set", "changelog_view", tt.setValue, "--data-dir", dir})
			err := rootCmd.Execute()

			if tt.wantErr {
				if err == nil {
					t.Fatalf("expected error for value %q", tt.setValue)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			loaded, err := config.Load(dir)
			if err != nil {
				t.Fatalf("Load after set: %v", err)
			}
			if loaded.ChangelogView != tt.wantState {
				t.Errorf("ChangelogView = %q, want %q", loaded.ChangelogView, tt.wantState)
			}
		})
	}
}

func TestConfigGetChangelogViewDefault(t *testing.T) {
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "get", "changelog_view", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := strings.TrimSpace(buf.String())
	if got != "highlights" {
		t.Errorf("config get changelog_view (default) = %q, want highlights", got)
	}
}

func TestConfigGetChangelogViewSet(t *testing.T) {
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.ChangelogView = "commits"
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "get", "changelog_view", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := strings.TrimSpace(buf.String())
	if got != "commits" {
		t.Errorf("config get changelog_view = %q, want commits", got)
	}
}

func TestConfigUnsetChangelogView(t *testing.T) {
	dir := t.TempDir()
	state := config.DefaultState()
	state.EncryptSecrets = false
	state.DataDir = dir
	state.ChangelogView = "commits"
	if err := config.Save(state); err != nil {
		t.Fatal(err)
	}

	var buf bytes.Buffer
	rootCmd.SetOut(&buf)
	rootCmd.SetErr(&buf)
	rootCmd.SetArgs([]string{"config", "unset", "changelog_view", "--data-dir", dir})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	loaded, err := config.Load(dir)
	if err != nil {
		t.Fatalf("Load after unset: %v", err)
	}
	if loaded.ChangelogView != "" {
		t.Errorf("ChangelogView after unset = %q, want empty", loaded.ChangelogView)
	}
	if loaded.ChangelogViewOrDefault() != "highlights" {
		t.Errorf("ChangelogViewOrDefault after unset = %q, want highlights", loaded.ChangelogViewOrDefault())
	}
}
