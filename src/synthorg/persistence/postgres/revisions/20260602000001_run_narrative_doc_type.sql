-- depends: 20260531000001_conversational_org_interface

-- Documentary mode (#1985): widen the project_docs.doc_type CHECK to
-- admit 'run_narrative' (the Chief-of-Staff run narrative) and
-- 'codebase_analysis' (the DocType member shipped without a matching
-- CHECK entry, so a brownfield-intake write would have failed at
-- insert). The inline column CHECK on the original revision is
-- auto-named project_docs_doc_type_check by Postgres.

ALTER TABLE project_docs
DROP CONSTRAINT project_docs_doc_type_check;

ALTER TABLE project_docs
ADD CONSTRAINT project_docs_doc_type_check CHECK (
    doc_type IN (
        'status_report',
        'deliverable',
        'knowledge_note',
        'codebase_analysis',
        'run_narrative'
    )
);
