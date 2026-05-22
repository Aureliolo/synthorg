-- depends: 20260521000001_external_api_governed_access

-- Persistent per-project reproducible-environment mapping (#1994). One
-- row per project (1:1), recording which declaration format provisioned
-- the environment, a content hash of the declaration so an unchanged
-- declaration short-circuits re-provision, and the built image reference
-- for the devcontainer image-build path. The declaration files live in
-- the git-backed workspace; this row is the durable provisioning cache.
-- ON DELETE CASCADE: deleting a project drops its environment mapping.

CREATE TABLE project_environments (
    project_id TEXT NOT NULL PRIMARY KEY
        CHECK (length(trim(project_id)) > 0),
    environment_type TEXT NOT NULL
        CHECK (environment_type IN ('manifest', 'devcontainer', 'nix')),
    declaration_hash TEXT NOT NULL
        CHECK (length(trim(declaration_hash)) > 0),
    image_ref TEXT,
    provisioned_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (updated_at >= provisioned_at),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX idx_project_environments_declaration_hash
    ON project_environments(declaration_hash);
