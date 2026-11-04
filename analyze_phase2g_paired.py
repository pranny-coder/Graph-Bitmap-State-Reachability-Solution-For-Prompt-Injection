import csv
import random
import statistics
from collections import defaultdict
from pathlib import Path

FILES = [
    Path("results/phase2g_validation_10.csv"),
    Path("results/phase2g_validation_40.csv"),
    Path("results/phase2g_validation_70.csv"),
]
BOOTSTRAPS = 5000
rng = random.Random(20261026)

# Sum event timings for each complete graph/seed/repeat/method sequence.
totals = defaultdict(float)

for path in FILES:
    if not path.exists():
        raise SystemExit(f"Missing benchmark file: {path}")

    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (
                path.stem,
                int(row["n"]),
                int(row["degree"]),
                int(row["checkpoints"]),
                int(row["seed"]),
                int(row["repeat"]),
                row["method"],
            )
            totals[key] += float(row["elapsed_ms"])

comparisons = [
    ("legacy_incremental", "support_count"),
    ("pruned_removal", "support_count"),
    ("legacy_incremental", "pruned_removal"),
]

def percentile(values, p):
    values = sorted(values)
    position = (len(values) - 1) * p
    lo = int(position)
    hi = min(lo + 1, len(values))
    weight = position - lo
    return values[lo] * (1 - weight) + values[hi] * weight

for filename in [p.stem for p in FILES]:
    print(f"\n=== {filename} ===")
    configs = sorted({
        key[1:4] for key in totals if key[0] == filename
    })

    for n, degree, checkpoints in configs:
        for baseline, candidate in comparisons:
            # Create paired speed ratios for each seed, using the median
            # ratio across repeats to reduce sensitivity to one noisy run.
            seed_ratios = defaultdict(list)

            for key, elapsed in totals.items():
                file, nn, dd, cp, seed, repeat, method = key
                if (
                    file != filename or nn != n or dd != degree
                    or cp != checkpoints
                ):
                    continue

                if method != baseline:
                    continue

                candidate_key = (
                    file, nn, dd, cp, seed, repeat, candidate
                )
                if candidate_key not in totals:
                    continue

                denominator = totals[candidate_key]
                if denominator > 0:
                    seed_ratios[seed].append(elapsed / denominator)

            per_seed = [
                statistics.median(values)
                for values in seed_ratios.values()
                if values
            ]

            if len(per_seed) < 2:
                print(f"{n=} {degree=} {baseline} vs {candidate}: "
                      "insufficient paired data")
                continue

            median_speedup = statistics.median(per_seed)
            win_rate = sum(x > 1 for x in per_seed) / len(per_seed)

            bootstrap_medians = []
            for _ in range(BOOTSTRAPS):
                sample = [
                    rng.choice(per_seed) for _ in range(len(per_seed))
                ]
                bootstrap_medians.append(statistics.median(sample))

            low = percentile(bootstrap_medians, 0.025)
            high = percentile(bootstrap_medians, 0.975)

            print(
                f"n={n:<5} degree={degree:<2} "
                f"{candidate:<16} vs {baseline:<16} "
                f"speedup={median_speedup:.3f}x "
                f"95% bootstrap CI=[{low:.3f}, {high:.3f}] "
                f"seed-win-rate={win_rate:.0%}"
            )

print("\nInterpretation: speedup > 1 favors the candidate.")
print("These are bootstrap intervals across seeds, not formal proof")
print("of generalization; the benchmark configurations are synthetic.")
