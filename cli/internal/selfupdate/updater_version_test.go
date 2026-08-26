package selfupdate

import "testing"

func TestIsUpdateAvailable(t *testing.T) {
	tests := []struct {
		current string
		latest  string
		want    bool
		wantErr bool
	}{
		{"dev", "v1.0.0", true, false},
		{"v1.0.0", "v1.0.0", false, false},
		{"v1.0.0", "v1.1.0", true, false},
		{"v1.0.0", "v2.0.0", true, false},
		{"v1.0.0", "v1.0.1", true, false},
		{"v2.0.0", "v1.0.0", false, false},                  // downgrade prevented
		{"v1.1.0", "v1.0.0", false, false},                  // downgrade prevented
		{"v1.0.1", "v1.0.0", false, false},                  // downgrade prevented
		{"v1.10.0", "v1.9.0", false, false},                 // multi-digit minor downgrade
		{"0.4.8-dev.4", "v0.4.8", true, false},              // stable release updates dev build
		{"0.4.8-dev.4", "v0.4.9", true, false},              // higher stable updates dev build
		{"0.4.8-dev.4", "v0.4.7", false, false},             // lower stable does not downgrade dev build
		{"0.4.8-dev.4", "v0.4.8-dev.4", false, false},       // same dev version, no update
		{"v0.4.7", "v0.4.7-dev.3", false, false},            // stable beats dev at same base
		{"v0.4.6", "v0.4.7-dev.1", true, false},             // dev for higher base is an update
		{"v0.4.7-dev.2", "v0.4.7-dev.3", true, false},       // higher dev number is an update
		{"v0.4.7-dev.3", "v0.4.7-dev.2", false, false},      // lower dev number is not
		{"v0.4.7-dev.3", "v0.4.7", true, false},             // stable release is an update from dev
		{"v1.0.0", "99999999999999999999.0.0", false, true}, // overflow in latest
		{"99999999999999999999.0.0", "v1.0.0", false, true}, // overflow in current
	}
	for _, tt := range tests {
		t.Run(tt.current+"->"+tt.latest, func(t *testing.T) {
			got, err := IsNewer(tt.current, tt.latest)
			if tt.wantErr {
				if err == nil {
					t.Fatal("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("IsNewer(%q, %q) unexpected error: %v", tt.current, tt.latest, err)
			}
			if got != tt.want {
				t.Errorf("IsNewer(%q, %q) = %v, want %v", tt.current, tt.latest, got, tt.want)
			}
		})
	}
}

func TestSplitDev(t *testing.T) {
	tests := []struct {
		input    string
		wantNum  int
		wantBase string
	}{
		{"0.4.7-dev.3", 3, "0.4.7"},
		{"0.4.7-dev.1", 1, "0.4.7"},
		{"0.4.7-dev.0", 0, "0.4.7"},
		{"0.4.7", -1, "0.4.7"},
		{"1.0.0", -1, "1.0.0"},
		{"0.4.7-dev.", -1, "0.4.7"},    // empty suffix: base extracted, treated as stable
		{"0.4.7-dev.NaN", -1, "0.4.7"}, // non-numeric: base extracted, treated as stable
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			gotNum, gotBase := splitDev(tt.input)
			if gotNum != tt.wantNum || gotBase != tt.wantBase {
				t.Errorf("splitDev(%q) = (%d, %q), want (%d, %q)", tt.input, gotNum, gotBase, tt.wantNum, tt.wantBase)
			}
		})
	}
}

func TestCompareWithDev(t *testing.T) {
	tests := []struct {
		name    string
		a       string
		b       string
		wantCmp int // >0, 0, <0
		wantErr bool
	}{
		{"stable beats same-base dev", "v0.4.7", "v0.4.7-dev.3", 1, false},
		{"higher dev beats lower dev", "v0.4.7-dev.3", "v0.4.7-dev.2", 1, false},
		{"lower dev loses to higher dev", "v0.4.7-dev.2", "v0.4.7-dev.3", -1, false},
		{"same dev equal", "v0.4.7-dev.3", "v0.4.7-dev.3", 0, false},
		{"lower base loses despite stable", "v0.4.6", "v0.4.7-dev.1", -1, false},
		{"higher base wins despite dev", "v0.5.0", "v0.4.7-dev.99", 1, false},
		{"both stable equal", "v0.4.7", "v0.4.7", 0, false},
		{"both stable different", "v0.4.8", "v0.4.7", 1, false},
		{"overflow propagates error", "99999999999999999999.0.0", "v0.4.7", 0, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := compareWithDev(tt.a, tt.b)
			if tt.wantErr {
				if err == nil {
					t.Fatal("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			switch {
			case tt.wantCmp > 0 && got <= 0:
				t.Errorf("compareWithDev(%q, %q) = %d, want > 0", tt.a, tt.b, got)
			case tt.wantCmp < 0 && got >= 0:
				t.Errorf("compareWithDev(%q, %q) = %d, want < 0", tt.a, tt.b, got)
			case tt.wantCmp == 0 && got != 0:
				t.Errorf("compareWithDev(%q, %q) = %d, want 0", tt.a, tt.b, got)
			}
		})
	}
}

func TestCompareSemver(t *testing.T) {
	tests := []struct {
		name    string
		a       string
		b       string
		wantCmp int // >0, 0, <0
		wantErr bool
	}{
		{"equal", "1.0.0", "1.0.0", 0, false},
		{"a greater major", "2.0.0", "1.0.0", 1, false},
		{"b greater major", "1.0.0", "2.0.0", -1, false},
		{"a greater minor", "1.2.0", "1.1.0", 1, false},
		{"a greater patch", "1.0.2", "1.0.1", 1, false},
		{"with v prefix", "v1.0.0", "v1.0.0", 0, false},
		{"pre-release suffix", "1.0.0-rc1", "1.0.0", 0, false},
		{"empty strings", "", "", 0, false},
		{"single component", "1", "2", -1, false},
		{"two components", "1.2", "1.1", 1, false},
		{"overflow version a", "99999999999999999999.0.0", "1.0.0", 0, true},
		{"overflow version b", "1.0.0", "99999999999999999999.0.0", 0, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := compareSemver(tt.a, tt.b)
			if tt.wantErr {
				if err == nil {
					t.Fatal("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			// Check sign rather than exact value.
			switch {
			case tt.wantCmp > 0 && got <= 0:
				t.Errorf("compareSemver(%q, %q) = %d, want > 0", tt.a, tt.b, got)
			case tt.wantCmp < 0 && got >= 0:
				t.Errorf("compareSemver(%q, %q) = %d, want < 0", tt.a, tt.b, got)
			case tt.wantCmp == 0 && got != 0:
				t.Errorf("compareSemver(%q, %q) = %d, want 0", tt.a, tt.b, got)
			}
		})
	}
}
