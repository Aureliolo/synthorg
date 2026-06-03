# Benchmark cassettes

Recorded real-run cassettes for measured per-model benchmark scores (#2204), one
JSON file per measured model id (e.g. `example-large-001.json`).

These are produced by `make record-benchmark-scores ARGS=--record` (real provider
spend) and replayed deterministically by `make record-benchmark-scores` to
regenerate `src/synthorg/budget/benchmark_seed.json`. The cassette file name must
match the `cassette:` path in `evals/benchmark_scores/models.yaml`.

Scores are measured from these recorded runs, never fitted.
