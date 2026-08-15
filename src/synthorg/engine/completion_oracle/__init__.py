# module-kind: feature
"""Build/test/review completion oracle.

Two composed layers make "done" mean *compiles + tests pass + an
independent peer review approves*, sourced from real verification rather
than an artifact count:

* Layer 1 (``classifier`` + ``evaluator`` + ``build_test_models``): a
  cheap, deterministic build/test gate that is a pure function of task
  classification and the already-persisted ``CodeExecutionRecord`` rows.
  It fails CLOSED for a code task whose tests failed or never ran.
* Layer 2 (``gate`` + ``runner`` + ``tools`` + ``review_models``): an
  independent agent-session peer reviewer, selected per review from the
  roster agents holding the ``Completion Reviewer`` role, that reads the
  deliverable, may run the tests, and files an approve / reject verdict via
  a single terminal tool.

The package ``__init__`` stays intentionally import-light: persistence
imports the ``review_models`` leaf, so eagerly loading the heavy gate /
runner here would create a cold-import cycle. Import the concrete
symbols from their leaf modules.
"""
