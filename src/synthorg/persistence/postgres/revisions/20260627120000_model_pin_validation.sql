-- Prompt-class pin-validation results: one row per prompt_class_id,
-- written by the pin-validation benchmark on a clean drift grade so
-- model_version_pinned_at means "last validated", not "the day we wrote it".

CREATE TABLE model_pin_validations (
    prompt_class_id TEXT NOT NULL PRIMARY KEY
    CHECK (CHAR_LENGTH(TRIM(prompt_class_id)) > 0),
    validated_at TIMESTAMPTZ NOT NULL,
    tier TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(tier)) > 0),
    passed BOOLEAN NOT NULL
);
