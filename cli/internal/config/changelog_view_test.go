package config

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestChangelogViewOrDefault(t *testing.T) {
	tests := []struct {
		name   string
		stored string
		want   string
	}{
		{"empty_default_to_highlights", "", "highlights"},
		{"explicit_highlights", "highlights", "highlights"},
		{"explicit_commits", "commits", "commits"},
		{"unknown_falls_back_to_default", "garbage", "highlights"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s := State{ChangelogView: tt.stored}
			if got := s.ChangelogViewOrDefault(); got != tt.want {
				t.Errorf("ChangelogViewOrDefault() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestIsValidChangelogView(t *testing.T) {
	for _, valid := range []string{"highlights", "commits"} {
		if !IsValidChangelogView(valid) {
			t.Errorf("IsValidChangelogView(%q) = false, want true", valid)
		}
	}
	for _, invalid := range []string{"", "Highlights", "HIGHLIGHTS", "foo", "auto"} {
		if IsValidChangelogView(invalid) {
			t.Errorf("IsValidChangelogView(%q) = true, want false", invalid)
		}
	}
}

func TestChangelogViewNames(t *testing.T) {
	got := ChangelogViewNames()
	for _, want := range []string{"highlights", "commits"} {
		if !strings.Contains(got, want) {
			t.Errorf("ChangelogViewNames() = %q, want to contain %q", got, want)
		}
	}
}

func TestChangelogViewValidation(t *testing.T) {
	base := DefaultState()

	t.Run("empty_passes", func(t *testing.T) {
		s := base
		s.ChangelogView = ""
		if err := s.Validate(); err != nil {
			t.Errorf("validate(empty) = %v, want nil", err)
		}
	})
	t.Run("highlights_passes", func(t *testing.T) {
		s := base
		s.ChangelogView = "highlights"
		if err := s.Validate(); err != nil {
			t.Errorf("validate(highlights) = %v, want nil", err)
		}
	})
	t.Run("commits_passes", func(t *testing.T) {
		s := base
		s.ChangelogView = "commits"
		if err := s.Validate(); err != nil {
			t.Errorf("validate(commits) = %v, want nil", err)
		}
	})
	t.Run("invalid_rejected", func(t *testing.T) {
		s := base
		s.ChangelogView = "foo"
		err := s.Validate()
		if err == nil {
			t.Fatal("validate(foo) = nil, want error")
		}
		if !strings.Contains(err.Error(), "changelog_view") {
			t.Errorf("error %v should mention changelog_view", err)
		}
	})
}

func TestChangelogViewJSONRoundTrip(t *testing.T) {
	tests := []struct {
		name  string
		value string
	}{
		{"empty_omitted", ""},
		{"highlights", "highlights"},
		{"commits", "commits"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s := State{ChangelogView: tt.value}
			data, err := json.Marshal(s)
			if err != nil {
				t.Fatalf("Marshal: %v", err)
			}
			// Empty value uses ,omitempty -- the field should not appear in JSON.
			if tt.value == "" && strings.Contains(string(data), `"changelog_view"`) {
				t.Errorf("empty ChangelogView should be omitted, got %s", data)
			}
			var s2 State
			if err := json.Unmarshal(data, &s2); err != nil {
				t.Fatalf("Unmarshal: %v", err)
			}
			if s2.ChangelogView != tt.value {
				t.Errorf("round-trip ChangelogView = %q, want %q", s2.ChangelogView, tt.value)
			}
		})
	}
}
