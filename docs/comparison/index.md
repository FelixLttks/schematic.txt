# Layout engine comparison

Baseline engine: `558353d` (first value) vs. current engine (second value). All drawings are scored with the current metrics (`schematic/layout/metrics.py`).

| Case | Score | Cross | Bends | Jogs | Near | Tight | Detour | Parallel | Viol. | Time (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| esp32_sd_spi | 30.4 → 65.0 | 9 → 0 | 19 → 14 | 3 → 0 | 3 → 3 | 3 → 0 | 1.11 → 1.21 | 0.73 → 0.93 | 0 → 0 | 0.08 → 0.45 |
| esp32_sensors_bus | 21.9 → 37.3 | 15 → 11 | 19 → 17 | 2 → 1 | 14 → 5 | 6 → 2 | 1.27 → 1.35 | 0.85 → 0.96 | 0 → 0 | 0.07 → 0.83 |
| example_large | 9.6 → 39.3 | 57 → 29 | 78 → 56 | 5 → 2 | 23 → 18 | 19 → 6 | 1.32 → 1.20 | 0.69 → 0.85 | 2 → 0 | 0.37 → 5.96 |
| simple_quickstart | 32.5 → 43.0 | 7 → 4 | 10 → 13 | 1 → 1 | 6 → 3 | 3 → 1 | 1.17 → 1.26 | 0.90 → 0.94 | 0 → 0 | 0.02 → 0.30 |
| 01_linear_chain | 69.8 → 100.0 | 0 → 0 | 4 → 0 | 0 → 0 | 1 → 0 | 1 → 0 | 1.00 → 1.00 | n/a → n/a | 0 → 0 | 0.01 → 0.05 |
| 02_branching | 75.7 → 72.5 | 1 → 2 | 7 → 3 | 0 → 0 | 0 → 0 | 0 → 1 | 1.11 → 1.12 | n/a → n/a | 0 → 0 | 0.03 → 0.19 |
| 03_dense_network | 6.8 → 48.6 | 60 → 7 | 35 → 28 | 5 → 1 | 22 → 10 | 15 → 5 | 1.07 → 1.39 | 0.82 → 0.98 | 2 → 0 | 0.28 → 2.03 |
| 04_parallel_bundle | 21.2 → 100.0 | 13 → 0 | 21 → 0 | 0 → 0 | 0 → 0 | 0 → 0 | 1.00 → 1.00 | 0.85 → 1.00 | 9 → 0 | 0.40 → 0.07 |
| 05_i2c_bus | 26.4 → 38.0 | 17 → 14 | 25 → 24 | 5 → 2 | 8 → 5 | 4 → 2 | 1.36 → 1.48 | 0.52 → 0.91 | 0 → 0 | 0.09 → 1.14 |
| 06_differential_pairs | 51.9 → 64.2 | 3 → 1 | 14 → 9 | 1 → 2 | 2 → 1 | 1 → 0 | 1.23 → 1.15 | 0.79 → 0.85 | 0 → 0 | 0.02 → 0.33 |
| 07_crossing_prone | 16.4 → 33.2 | 6 → 6 | 26 → 23 | 0 → 1 | 4 → 5 | 3 → 2 | 1.23 → 1.24 | 0.38 → 0.47 | 4 → 0 | 0.10 → 0.47 |
| 08_clusters | 64.8 → 91.6 | 2 → 0 | 17 → 4 | 0 → 0 | 1 → 1 | 0 → 1 | 1.07 → 1.00 | 0.69 → 0.98 | 0 → 0 | 0.04 → 0.13 |
| 09_isolated | 34.6 → 72.3 | 1 → 0 | 8 → 4 | 1 → 0 | 1 → 1 | 1 → 0 | 1.32 → 1.08 | n/a → n/a | 0 → 0 | 0.01 → 0.06 |
| 10_long_distance | 27.2 → 54.0 | 8 → 3 | 24 → 13 | 3 → 1 | 9 → 4 | 4 → 2 | 1.44 → 1.28 | n/a → n/a | 0 → 0 | 0.03 → 0.65 |
| 11_length_vs_readability | 60.0 → 61.1 | 0 → 0 | 6 → 8 | 0 → 0 | 2 → 2 | 2 → 2 | 1.00 → 1.18 | 0.62 → 0.95 | 0 → 0 | 0.01 → 0.08 |
| 12_labels_annotations | 20.5 → 67.6 | 10 → 1 | 25 → 13 | 4 → 0 | 13 → 3 | 5 → 1 | 1.25 → 1.19 | 0.75 → 1.00 | 0 → 0 | 0.04 → 0.49 |
| 13_functional_regions | 23.7 → 47.2 | 20 → 8 | 21 → 36 | 3 → 2 | 12 → 5 | 6 → 3 | 0.83 → 1.39 | 0.97 → 0.86 | 17 → 0 | 4.42 → 2.74 |

## esp32_sd_spi

**Before** (score 30.4)

![before](esp32_sd_spi.before.svg)

**After** (score 65.0)

![after](esp32_sd_spi.after.svg)

## esp32_sensors_bus

**Before** (score 21.9)

![before](esp32_sensors_bus.before.svg)

**After** (score 37.3)

![after](esp32_sensors_bus.after.svg)

## example_large

**Before** (score 9.6)

![before](example_large.before.svg)

**After** (score 39.3)

![after](example_large.after.svg)

## simple_quickstart

**Before** (score 32.5)

![before](simple_quickstart.before.svg)

**After** (score 43.0)

![after](simple_quickstart.after.svg)

## 01_linear_chain

**Before** (score 69.8)

![before](01_linear_chain.before.svg)

**After** (score 100.0)

![after](01_linear_chain.after.svg)

## 02_branching

**Before** (score 75.7)

![before](02_branching.before.svg)

**After** (score 72.5)

![after](02_branching.after.svg)

## 03_dense_network

**Before** (score 6.8)

![before](03_dense_network.before.svg)

**After** (score 48.6)

![after](03_dense_network.after.svg)

## 04_parallel_bundle

**Before** (score 21.2)

![before](04_parallel_bundle.before.svg)

**After** (score 100.0)

![after](04_parallel_bundle.after.svg)

## 05_i2c_bus

**Before** (score 26.4)

![before](05_i2c_bus.before.svg)

**After** (score 38.0)

![after](05_i2c_bus.after.svg)

## 06_differential_pairs

**Before** (score 51.9)

![before](06_differential_pairs.before.svg)

**After** (score 64.2)

![after](06_differential_pairs.after.svg)

## 07_crossing_prone

**Before** (score 16.4)

![before](07_crossing_prone.before.svg)

**After** (score 33.2)

![after](07_crossing_prone.after.svg)

## 08_clusters

**Before** (score 64.8)

![before](08_clusters.before.svg)

**After** (score 91.6)

![after](08_clusters.after.svg)

## 09_isolated

**Before** (score 34.6)

![before](09_isolated.before.svg)

**After** (score 72.3)

![after](09_isolated.after.svg)

## 10_long_distance

**Before** (score 27.2)

![before](10_long_distance.before.svg)

**After** (score 54.0)

![after](10_long_distance.after.svg)

## 11_length_vs_readability

**Before** (score 60.0)

![before](11_length_vs_readability.before.svg)

**After** (score 61.1)

![after](11_length_vs_readability.after.svg)

## 12_labels_annotations

**Before** (score 20.5)

![before](12_labels_annotations.before.svg)

**After** (score 67.6)

![after](12_labels_annotations.after.svg)

## 13_functional_regions

**Before** (score 23.7)

![before](13_functional_regions.before.svg)

**After** (score 47.2)

![after](13_functional_regions.after.svg)
