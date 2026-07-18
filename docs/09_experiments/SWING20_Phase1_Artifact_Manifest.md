# SWING_20 Phase 1 Artifact Manifest

**Purpose:** SHA-256 checksums for the generated artifacts behind EXP-001, EXP-002,
and EXP-003, so a locally-archived copy can be verified byte-identical to the one the
experiment reports and decisions were computed from, without committing the artifact
itself to git.

**These artifacts are intentionally excluded from git** (see `.gitignore`:
`artifacts/`). They are generated output, not source code or a normative document --
reproducible from the committed scripts below, and the durable findings/decisions are
already recorded in `docs/09_experiments/EXP-001*.md`, `EXP-002*.md`, `EXP-003*.md`.
Keep the actual files in a local or external artifact archive (not git) for as long as
they remain useful as an audit trail; this manifest lets that archive be verified
later even if the files are moved or copied.

All paths below are repo-relative; none are absolute local filesystem paths.

---

## JSON reports

### `artifacts/swing_20_feature_replication_report.json`

```text
size:   8,835 bytes
sha256: 5c59ceeca81e088931ab22ba902006d851d6cdb667f9065058f6cb825c1d5bcb
experiment: EXP-001 (MF1/VC3 replication)
source feature dataset: artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet
reproduction:
  python scripts/analyze_swing_20_feature_replication.py \
      --features-path artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet \
      --output-json artifacts/swing_20_feature_replication_report.json
```

### `artifacts/swing_20_context_target_mechanics_report.json`

```text
size:   35,526 bytes
sha256: cf8962c445199ef74b25575f4343b2e38163a936912ced2ea70da97ece49227b
experiment: EXP-001 (H1/H2/H3 Context and Target Mechanics cycle)
source feature dataset: artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet
reproduction:
  python scripts/analyze_swing_20_context_target_mechanics.py \
      --features-path artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet \
      --output-json artifacts/swing_20_context_target_mechanics_report.json
```

### `artifacts/swing_20_logistic_baseline_report.json`

```text
size:   105,169 bytes
sha256: 8545884fb602af9625daa87ef6880773e96829a872a81f6fbe7818e1df3ccd7d
experiment: EXP-002 (Model 0/1/2 Logistic Regression baseline, daily cross-sectional evaluation)
source feature dataset: artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet
reproduction:
  python scripts/train_swing_20_logistic_baseline.py \
      --features-path artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet \
      --output-json artifacts/swing_20_logistic_baseline_report.json
```

### `artifacts/swing_20_locked_test_report.json`

```text
size:   32,969 bytes
sha256: a040484e3a0bf95614c44383a6aca4d06c02e0d14131f9d69895fdc91a8f9a5e
experiment: EXP-003 (Model 2 Locked Test -- final result, PASS)
source feature datasets:
  train: artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet
  locked_test: artifacts/swing_20_features_locked_test/snapshots/swing20_locked_test_features_20260718T183948Z/features.parquet
reproduction (verifies byte-identical output; does NOT re-open a new Locked Test --
locked_test is read once per the EXP-003 pre-registration, and this command only
re-derives the same deterministic result from data already read):
  python scripts/evaluate_swing_20_locked_test.py \
      --train-features-path artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet \
      --locked-test-features-path artifacts/swing_20_features_locked_test/snapshots/swing20_locked_test_features_20260718T183948Z/features.parquet \
      --output-json artifacts/swing_20_locked_test_report.json
```

---

## Feature dataset parquet files

### `artifacts/swing_20_features/snapshots/swing20_features_20260718T165654Z/features.parquet`

```text
size:   173,741,881 bytes (~166 MB)
sha256: b5d84fb0ee3b29bdd2b15f82c2d1c85904d569cc4629e4891a1d559be5806d6d
rows:   1,655,036
symbols: 3,347
split:  train + validation only (locked_test deliberately excluded from this build)
experiments: EXP-001, EXP-002 (source data for all validation-phase reports above)
source SWING_20 snapshot: swing20_20260718T135238Z (artifacts/swing_20/snapshots/swing20_20260718T135238Z/)
reproduction:
  python scripts/build_swing_20_feature_dataset.py \
      --dataset-dir artifacts/swing_20/snapshots/swing20_20260718T135238Z \
      --progress-every 200
  (writes a new artifacts/swing_20_features/snapshots/<timestamp>/features.parquet;
  content should match this checksum, but the directory name will differ)
```

### `artifacts/swing_20_features_locked_test/snapshots/swing20_locked_test_features_20260718T183948Z/features.parquet`

```text
size:   51,695,466 bytes (~50 MB)
sha256: d54637774024549fc274ed58e1a3166c06e6aa1967e80fa2369e682fbf480568
rows:   493,651
symbols: 3,227
split:  locked_test only
date range: 2025-09-04 .. 2026-06-17 (198 dates)
experiment: EXP-003 (Locked Test)
source SWING_20 snapshot: swing20_20260718T135238Z (artifacts/swing_20/snapshots/swing20_20260718T135238Z/)
reproduction:
  python scripts/build_swing_20_locked_test_features.py \
      --dataset-dir artifacts/swing_20/snapshots/swing20_20260718T135238Z \
      --progress-every 200
  (writes a new artifacts/swing_20_features_locked_test/snapshots/<timestamp>/features.parquet)
```

---

## Not included in this manifest

- `artifacts/swing_20_features/snapshots/swing20_features_20260718T164705Z/` -- the
  20-symbol deterministic integration sample used to validate the build pipeline
  before scaling to the full population (EXP-001 development step). Not a source for
  any committed experiment result; omitted here as low-value to checksum. Not
  committed to git either.
- `artifacts/swing_20/snapshots/swing20_20260718T135238Z/` -- the frozen SWING_20
  dataset snapshot itself (pre-dates this manifest; already hash-verified by its own
  `manifest.json` per the dataset versioning system described in
  `docs/02_mvp/SWING20_Dataset_Specification_v1.md`).
