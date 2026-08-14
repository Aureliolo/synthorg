# Inner execution-loop A/B scoreboard

- Measured against commit `cdfb2e1a70f54c3fa51b92c785005cb7458ba96d`
- Generated 2026-08-12T07:58:11.075054+00:00
- Brief suite `sha256:113894e4cfda6693`
- Manifest `sha256:2ae4b7df1b62d6968c2543400813ec54ba529606431884e10dd0fc2c9332937e`
- Images: sandbox `ghcr.io/aureliolo/synthorg-sandbox@sha256:1b2a7c3bb65cd50b7b03368df9ad3b0312bd9e1ce57721e24ea50c640dc9d1fb` (`sha256:1b2a7c3bb65cd50b7b03368df9ad3b0312bd9e1ce57721e24ea50c640dc9d1fb`), sidecar `synthorg-sidecar:privdrop` (`sha256:eff6ab76f158772ceaef88ee028ade053d40a055ee15bc38cf3d27872b8b1438`), OpenHands `synthorg-openhands:parity` (`sha256:ac3a3df36e07ba90ee68120f0e4178e9d3eb629fd0fda64f0152f2b00faf134f`)
- Rubric weights: correctness 60, tokens 10, latency 5, turns 5, resilience 20 (gate floor 60)
- Total measured spend: 0.0000

## Results

| Brief | Capability | Loop | Score | Correctness | Tokens | Wall-clock | Turns | Rework | Pass rate | Spend |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| loop-ab-bugfix | expert | react | 100.0 | 100 | 33001 | 32.5s | 11 | 0+ | 100% | 0.0000 |
| loop-ab-bugfix | expert | openhands | 90.3 | 100 | 95014 | 63.6s | 13 | 0+ | 100% | 0.0000 |
| loop-ab-bugfix | capable | react | 94.7 | 100 (40-100) | 34948 | 32.6s | 13 | 0+ | 67% | 0.0000 |
| loop-ab-bugfix | capable | openhands | 85.7 | 100 | 227199 | 108.3s | 24 | 0+ | 100% | 0.0000 |
| loop-ab-bugfix | basic | react | 96.4 | 100 | 99629 | 34.9s | 27 | 8+ | 100% | 0.0000 |
| loop-ab-bugfix | basic | openhands | 91.4 | 100 | 236136 | 78.9s | 27 | 0+ | 100% | 0.0000 |
| loop-ab-feature | expert | react | 100.0 | 100 | 26487 | 44.1s | 8 | 0+ | 100% | 0.0000 |
| loop-ab-feature | expert | openhands | 91.4 | 100 | 73373 | 53.4s | 11 | 0+ | 100% | 0.0000 |
| loop-ab-feature | capable | openhands | 83.5 | 100 | 163569 | 84.6s | 17 | 0+ | 100% | 0.0000 |
| loop-ab-feature | capable | react (disqualified) | 41.3 | 20 (20-100) | 13319 | 10.7s | 7 | 0+ | 33% | 0.0000 |
| loop-ab-feature | basic | react | 93.8 | 100 | 389391 | 73.6s | 49 | 23+ | 100% | 0.0000 |
| loop-ab-feature | basic | openhands | 92.2 | 100 (40-100) | 348128 | 148.0s | 36 | 0+ | 67% | 0.0000 |
| loop-ab-pipeline | expert | react | 100.0 | 100 | 38902 | 38.2s | 9 | 0+ | 100% | 0.0000 |
| loop-ab-pipeline | expert | openhands | 91.1 | 100 | 94677 | 59.1s | 12 | 0+ | 100% | 0.0000 |
| loop-ab-pipeline | capable | openhands | 81.5 | 100 | 159330 | 90.3s | 18 | 0+ | 100% | 0.0000 |
| loop-ab-pipeline | capable | react (disqualified) | 41.3 | 20 (20-100) | 5094 | 7.1s | 3 | 0+ | 33% | 0.0000 |
| loop-ab-pipeline | basic | react (disqualified) | 37.5 | 20 (0-100) | 175370 | 72.5s | 32 | 8+ | 33% | 0.0000 |
| loop-ab-pipeline | basic | openhands (disqualified) | 32.3 | 20 (20-40) | 255176 | 82.1s | 30 | 0+ | 0% | 0.0000 |
| loop-ab-refactor | expert | react | 100.0 | 100 | 22587 | 26.5s | 8 | 0+ | 100% | 0.0000 |
| loop-ab-refactor | expert | openhands | 89.4 | 100 | 66360 | 55.4s | 11 | 0+ | 100% | 0.0000 |
| loop-ab-refactor | capable | react | 92.7 | 100 (20-100) | 68494 | 38.2s | 20 | 1+ | 67% | 0.0000 |
| loop-ab-refactor | capable | openhands | 91.4 | 100 | 152709 | 87.4s | 21 | 0+ | 100% | 0.0000 |
| loop-ab-refactor | basic | openhands (disqualified) | 34.2 | 20 (20-80) | 117796 | 44.1s | 15 | 0+ | 0% | 0.0000 |
| loop-ab-refactor | basic | react (disqualified) | 30.5 | 20 (20-80) | 96629 | 47.4s | 26 | 3+ | 0% | 0.0000 |
| loop-ab-simple | expert | react | 100.0 | 100 | 6748 | 14.8s | 4 | 0+ | 100% | 0.0000 |
| loop-ab-simple | expert | openhands | 89.1 | 100 | 27777 | 28.2s | 5 | 0+ | 100% | 0.0000 |
| loop-ab-simple | capable | react | 98.0 | 100 | 24950 | 22.7s | 10 | 0+ | 100% | 0.0000 |
| loop-ab-simple | capable | openhands | 94.6 | 100 | 37822 | 37.2s | 6 | 0+ | 100% | 0.0000 |
| loop-ab-simple | basic | react | 100.0 | 100 | 11075 | 18.8s | 5 | 0+ | 100% | 0.0000 |
| loop-ab-simple | basic | openhands | 88.4 | 100 (0-100) | 26607 | 20.3s | 5 | 0+ | 67% | 0.0000 |

`+` on Rework: provider retries are not observable for that loop, so the figure counts repeated tool calls only. Scoring drops the retry component for every loop in such a cell.

## Termination and governance

| Brief | Capability | Loop | Runs | Terminations | Artifacts | Governance events |
|---|---|---|---|---|---:|---|
| loop-ab-bugfix | expert | openhands | 3 | completed x3 | 100% | none |
| loop-ab-bugfix | expert | react | 3 | completed x3 | 100% | none |
| loop-ab-bugfix | capable | openhands | 3 | completed x3 | 100% | none |
| loop-ab-bugfix | capable | react | 3 | completed x2, no_op x1 | 67% | none |
| loop-ab-bugfix | basic | openhands | 3 | completed x3 | 100% | none |
| loop-ab-bugfix | basic | react | 3 | completed x3 | 100% | none |
| loop-ab-feature | expert | openhands | 3 | completed x3 | 100% | none |
| loop-ab-feature | expert | react | 3 | completed x3 | 100% | none |
| loop-ab-feature | capable | openhands | 3 | completed x3 | 100% | none |
| loop-ab-feature | capable | react | 3 | completed x1, no_op x2 | 33% | none |
| loop-ab-feature | basic | openhands | 3 | completed x3 | 100% | none |
| loop-ab-feature | basic | react | 3 | completed x3 | 100% | none |
| loop-ab-pipeline | expert | openhands | 3 | completed x3 | 100% | none |
| loop-ab-pipeline | expert | react | 3 | completed x3 | 100% | none |
| loop-ab-pipeline | capable | openhands | 3 | completed x3 | 100% | none |
| loop-ab-pipeline | capable | react | 3 | completed x1, no_op x2 | 33% | none |
| loop-ab-pipeline | basic | openhands | 3 | completed x2, no_op x1 | 67% | none |
| loop-ab-pipeline | basic | react | 3 | completed x1, error x1, no_op x1 | 67% | none |
| loop-ab-refactor | expert | openhands | 3 | completed x3 | 100% | none |
| loop-ab-refactor | expert | react | 3 | completed x3 | 100% | none |
| loop-ab-refactor | capable | openhands | 3 | completed x3 | 100% | none |
| loop-ab-refactor | capable | react | 3 | completed x2, no_op x1 | 67% | none |
| loop-ab-refactor | basic | openhands | 3 | completed x2, no_op x1 | 67% | none |
| loop-ab-refactor | basic | react | 3 | completed x2, error x1 | 100% | none |
| loop-ab-simple | expert | openhands | 3 | completed x3 | 100% | none |
| loop-ab-simple | expert | react | 3 | completed x3 | 100% | none |
| loop-ab-simple | capable | openhands | 3 | completed x3 | 100% | none |
| loop-ab-simple | capable | react | 3 | completed x3 | 100% | none |
| loop-ab-simple | basic | openhands | 3 | completed x3 | 100% | none |
| loop-ab-simple | basic | react | 3 | completed x3 | 100% | none |

## Spend by provider and model

| Provider | Model | Input tokens | Output tokens | Cost | Currency |
|---|---|---:|---:|---:|---|
| example-provider | example-expert-001 | 1433286 | 49388 | 0.0000 | USD |
| example-provider | example-capable-001 | 2883189 | 100482 | 0.0000 | USD |
| example-provider | example-basic-001 | 5788492 | 111760 | 0.0000 | USD |

## Promotion recommendation

Apply to the existing settings (no new selection machinery):

```ini
engine.default_loop_type = react
engine.loop_complexity_overrides =
```

Evidence, per complexity bucket:

| Complexity | Winning loop | Composite |
|---|---|---:|
| simple | react | 99.3 |
| capable | react | 97.0 |
