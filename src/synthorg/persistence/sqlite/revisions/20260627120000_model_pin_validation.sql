-- Prompt-class pin-validation results: one row per prompt_class_id,
-- written by the pin-validation benchmark on a clean drift grade so
-- model_version_pinned_at means "last validated", not "the day we wrote it".

CREATE TABLE model_pin_validations (
    prompt_class_id TEXT NOT NULL PRIMARY KEY
    CHECK (LENGTH(TRIM(prompt_class_id)) > 0),
    validated_at TEXT NOT NULL CHECK (
        validated_at LIKE '%+00:00' OR validated_at LIKE '%Z'
    ),
    tier TEXT NOT NULL CHECK (LENGTH(TRIM(tier)) > 0),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1))
);
