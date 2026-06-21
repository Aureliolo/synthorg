package config

import "testing"

func TestGenerateMasterKey(t *testing.T) {
	t.Parallel()
	k1, err := GenerateMasterKey()
	if err != nil {
		t.Fatalf("GenerateMasterKey: %v", err)
	}
	if err := validateFernetKey(k1); err != nil {
		t.Errorf("GenerateMasterKey produced an invalid Fernet key: %v", err)
	}
	k2, err := GenerateMasterKey()
	if err != nil {
		t.Fatalf("GenerateMasterKey (second): %v", err)
	}
	if k1 == k2 {
		t.Error("GenerateMasterKey should produce a unique key per call")
	}
}

func TestEnsureMasterKey(t *testing.T) {
	t.Parallel()
	t.Run("generates when encrypt on and key empty", func(t *testing.T) {
		s := State{EncryptSecrets: true}
		generated, err := EnsureMasterKey(&s)
		if err != nil {
			t.Fatalf("EnsureMasterKey: %v", err)
		}
		if !generated {
			t.Error("expected generated=true")
		}
		if err := validateFernetKey(s.MasterKey); err != nil {
			t.Errorf("generated MasterKey is invalid: %v", err)
		}
	})

	t.Run("no-op when key already present", func(t *testing.T) {
		existing := "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
		s := State{EncryptSecrets: true, MasterKey: existing}
		generated, err := EnsureMasterKey(&s)
		if err != nil {
			t.Fatalf("EnsureMasterKey: %v", err)
		}
		if generated {
			t.Error("expected generated=false when a key already exists")
		}
		if s.MasterKey != existing {
			t.Errorf("MasterKey mutated: got %q, want %q", s.MasterKey, existing)
		}
	})

	t.Run("no-op when encryption disabled", func(t *testing.T) {
		s := State{EncryptSecrets: false}
		generated, err := EnsureMasterKey(&s)
		if err != nil {
			t.Fatalf("EnsureMasterKey: %v", err)
		}
		if generated {
			t.Error("expected generated=false when EncryptSecrets is false")
		}
		if s.MasterKey != "" {
			t.Errorf("MasterKey should stay empty, got %q", s.MasterKey)
		}
	})

	t.Run("treats whitespace-only key as missing", func(t *testing.T) {
		s := State{EncryptSecrets: true, MasterKey: "   "}
		generated, err := EnsureMasterKey(&s)
		if err != nil {
			t.Fatalf("EnsureMasterKey: %v", err)
		}
		if !generated {
			t.Error("expected generated=true for a blank key")
		}
		if err := validateFernetKey(s.MasterKey); err != nil {
			t.Errorf("generated MasterKey is invalid: %v", err)
		}
	})
}
