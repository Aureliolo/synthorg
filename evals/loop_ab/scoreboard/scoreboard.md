# Inner execution-loop A/B scoreboard

- Measured against commit `4c4d7cc3ffcb89895c471b65b7aa84cbe3e59fb0` (dirty tree)
- Generated 2026-08-11T04:37:49.887529+00:00
- Brief suite `sha256:3c3e00788c0676b6`
- Manifest `sha256:d535afc6ebd7d37527e2e36eae253ad5840a86db5311ea93a53df89dc7578d0e`
- Images: sandbox `ghcr.io/aureliolo/synthorg-sandbox@sha256:1b2a7c3bb65cd50b7b03368df9ad3b0312bd9e1ce57721e24ea50c640dc9d1fb`, sidecar `synthorg-sidecar:privdrop`, OpenHands `ghcr.io/aureliolo/synthorg-openhands@sha256:bc5fc1a0b38ecb66f7ddb3a59540472b69710c40f292f645e81f6781b3876450`
- Rubric weights: correctness 60, tokens 15, latency 10, turns 10, resilience 5 (gate floor 60)
- Total measured spend: 0.0000

## Results

| Brief | Tier | Loop | Score | Correctness | Tokens | Wall-clock | Turns | Rework | Pass rate | Spend |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| loop-ab-bugfix | large | react (disqualified) | 61.0 | 40 | 3723 | 5.3s | 3 | 0+ | 0% | 0.0000 |
| loop-ab-bugfix | large | openhands (disqualified) | 29.7 | 40 | 131389 | 68.0s | 20 | 0+ | 33% | 0.0000 |
| loop-ab-bugfix | medium | openhands | 66.4 | 100 | 131226 | 57.2s | 20 | 0+ | 67% | 0.0000 |
| loop-ab-bugfix | medium | react (disqualified) | 61.0 | 40 | 3039 | 3.1s | 3 | 0+ | 0% | 0.0000 |
| loop-ab-bugfix | small | openhands | 99.0 | 100 | 155364 | 85.4s | 20 | 0+ | 67% | 0.0000 |
| loop-ab-bugfix | small | react | 81.6 | 100 | 267852 | 139.9s | 43 | 14+ | 67% | 0.0000 |
| loop-ab-feature | large | openhands | 69.4 | 100 | 64438 | 43.5s | 12 | 0+ | 67% | 0.0000 |
| loop-ab-feature | large | react (disqualified) | 49.0 | 20 | 4828 | 4.1s | 4 | 0+ | 0% | 0.0000 |
| loop-ab-feature | medium | openhands | 67.6 | 100 | 86012 | 44.8s | 12 | 0+ | 100% | 0.0000 |
| loop-ab-feature | medium | react (disqualified) | 49.0 | 20 | 2034 | 2.6s | 2 | 0+ | 0% | 0.0000 |
| loop-ab-feature | small | openhands (disqualified) | 45.1 | 20 | 51435 | 44.6s | 9 | 0+ | 0% | 0.0000 |
| loop-ab-feature | small | react (disqualified) | 39.6 | 20 | 59843 | 27.4s | 20 | 10+ | 0% | 0.0000 |
| loop-ab-simple | large | react | 97.1 | 100 | 9875 | 12.2s | 7 | 0+ | 100% | 0.0000 |
| loop-ab-simple | large | openhands | 86.6 | 100 | 24095 | 22.2s | 5 | 0+ | 100% | 0.0000 |
| loop-ab-simple | medium | react | 97.1 | 100 | 12490 | 12.1s | 7 | 0+ | 100% | 0.0000 |
| loop-ab-simple | medium | openhands | 87.2 | 100 | 25279 | 20.8s | 5 | 0+ | 67% | 0.0000 |
| loop-ab-simple | small | react | 100.0 | 100 | 11702 | 22.3s | 6 | 0+ | 100% | 0.0000 |
| loop-ab-simple | small | openhands | 86.7 | 100 | 32668 | 30.4s | 6 | 0+ | 67% | 0.0000 |

`+` on Rework: provider retries are not observable for that loop, so the figure counts repeated tool calls only. Scoring drops the retry component for every loop in such a cell.

## Termination and governance

| Brief | Tier | Loop | Terminations | Artifacts | Governance events |
|---|---|---|---|---:|---|
| loop-ab-bugfix | large | openhands | completed x1, max_turns x2 | 100% | none |
| loop-ab-bugfix | large | react | error x3 | 100% | none |
| loop-ab-bugfix | medium | openhands | completed x1, max_turns x2 | 100% | none |
| loop-ab-bugfix | medium | react | error x3 | 100% | none |
| loop-ab-bugfix | small | openhands | completed x1, max_turns x2 | 100% | none |
| loop-ab-bugfix | small | react | completed x3 | 100% | none |
| loop-ab-feature | large | openhands | completed x2, error x1 | 100% | none |
| loop-ab-feature | large | react | error x3 | 100% | none |
| loop-ab-feature | medium | openhands | completed x3 | 100% | none |
| loop-ab-feature | medium | react | error x3 | 100% | none |
| loop-ab-feature | small | openhands | error x2, max_turns x1 | 100% | none |
| loop-ab-feature | small | react | completed x1, error x2 | 100% | none |
| loop-ab-simple | large | openhands | completed x3 | 100% | none |
| loop-ab-simple | large | react | completed x3 | 100% | none |
| loop-ab-simple | medium | openhands | completed x2, error x1 | 67% | none |
| loop-ab-simple | medium | react | completed x3 | 100% | none |
| loop-ab-simple | small | openhands | completed x2, max_turns x1 | 100% | none |
| loop-ab-simple | small | react | completed x3 | 100% | none |

## Spend by provider and model

| Provider | Model | Input tokens | Output tokens | Cost | Currency |
|---|---|---:|---:|---:|---|
| example-provider | example-large-001 | 674210 | 14893 | 0.0000 | USD |
| example-provider | example-medium-001 | 730393 | 18139 | 0.0000 | USD |
| example-provider | example-small-001 | 1952892 | 49317 | 0.0000 | USD |

## Promotion recommendation

Apply to the existing settings (no new selection machinery):

```ini
engine.default_loop_type = react
engine.loop_complexity_overrides = 
```

Evidence, per complexity bucket:

| Complexity | Winning loop | Composite |
|---|---|---:|
| simple | react | 98.1 |
