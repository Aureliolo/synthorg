-- A backgrounded shell command outlives the tool call that started it, so
-- its record has to outlive the process too: this is what the boot
-- reconciliation sweep reads to tell "still running in a live container"
-- from "orphaned by a hard kill" (see ``reap_orphaned_background_jobs``).
--
-- ``container_id`` and ``owner_id`` are both indexed because they answer
-- two different questions the sandbox layer asks constantly: "does this
-- container still have live jobs pinning it open" (grace/idle timer
-- recheck, keyed on container_id+status) and "how many live jobs does
-- this owner already have" (the per-owner job cap, keyed on
-- owner_id+status). Both composite on ``status`` rather than plain on
-- the id column: a container reused across a long-lived per-agent
-- lifetime can accumulate rows well past one page, and a plain-id index
-- still leaves the live-status filter to a row-by-row scan of every
-- historical row instead of an index range seek.
--
-- ``status`` carries all seven values ``BackgroundJobStatus`` can write;
-- the CHECK is the DB-side half of the vocabulary parity the Postgres
-- twin of this revision must repeat exactly.
--
-- ``pid`` and ``exit_code`` are nullable: a job starts PENDING with
-- neither, gains a PID once the wrapper confirms the process started,
-- and gains an exit code only once it actually finishes.

CREATE TABLE background_jobs (
    job_id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(job_id)) > 0),
    container_id TEXT NOT NULL CHECK (LENGTH(TRIM(container_id)) > 0),
    owner_id TEXT NOT NULL CHECK (LENGTH(TRIM(owner_id)) > 0),
    project_id TEXT CHECK (project_id IS NULL OR LENGTH(TRIM(project_id)) > 0),
    command_repr TEXT NOT NULL CHECK (LENGTH(TRIM(command_repr)) > 0),
    pid INTEGER CHECK (pid IS NULL OR pid > 0),
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'running', 'completed', 'failed',
        'cancelled', 'timed_out', 'orphaned'
    )),
    exit_code INTEGER,
    output_path TEXT NOT NULL CHECK (LENGTH(TRIM(output_path)) > 0),
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    max_duration_seconds REAL NOT NULL CHECK (max_duration_seconds > 0)
);

CREATE INDEX idx_background_jobs_container_status ON background_jobs (container_id, status);
CREATE INDEX idx_background_jobs_owner_status ON background_jobs (owner_id, status);
