import csv
import random
import statistics
from collections import defaultdict
from pathlib import Path

BOOTSTRAPS = 5000
files = [
    p for p in sorted(Path("results").glob("phase2h_sweep_confirm_*.csv"))
    if not p.stem.endswith("_summary")
]

if not files:
    raise SystemExit("No phase2h_sweep_*.csv files found.")

def percentile(values, p):
    values = sorted(values)
    pos = (len(values) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    weight = pos - lo
    return values[lo] * (1 - weight) + values[hi] * weight

print(
    "setting  actual_remove  n     degree  support_speedup  "
    "95% bootstrap CI       seed_win_rate"
)

for path in files:
    # Sum per-event timing into complete sequences.
    totals = defaultdict(float)
    events = {}

    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n = int(row["n"])
            degree = int(row["degree"])
            cp = int(row["checkpoints"])
            seed = int(row["seed"])
            repeat = int(row["repeat"])
            method = row["method"]
            step = int(row["step"])
            operation = row["operation"]

            key = (n, degree, cp, seed, repeat, method)
            totals[key] += float(row["elapsed_ms"])

            # Event sequences repeat identically across methods and repeats.
            if method == "full_recompute" and repeat == 1:
                event_key = (n, degree, cp, seed, step)
                events[event_key] = operation

    counts = [0, 0]
    for operation in events.values():
        counts[0 if operation == "add" else 1] += 1

    additions, removals = counts
    event_total = additions + removals
    share = removals / event_total if event_total else 0

    rng = random.Random(20261026 + int(path.stem.rsplit("_", 1)[1]))

    configs = sorted({
        (key[0], key[1], key[2]) for key in totals
    })

    for n, degree, cp in configs:
        per_seed = defaultdict(list)

        for seed in sorted({
            key[3] for key in totals
            if key[:3] == (n, degree, cp)
        }):
            for repeat in sorted({
                key[4] for key in totals
                if key[:4] == (n, degree, cp, seed)
            }):
                legacy_key = (
                    n, degree, cp, seed, repeat, "legacy_incremental"
                )
                support_key = (
                    n, degree, cp, seed, repeat, "support_count"
                )

                if legacy_key not in totals or support_key not in totals:
                    continue

                legacy = totals[legacy_key]
                support = totals[support_key]

                if support > 0:
                    per_seed[seed].append(legacy / support)

        seed_ratios = [
            statistics.median(ratios)
            for ratios in per_seed.values() if ratios
        ]

        if len(seed_ratios) < 2:
            continue

        speedup = statistics.median(seed_ratios)

        boot = []
        for _ in range(BOOTSTRAPS):
            sample = [
                rng.choice(seed_ratios) for _ in range(len(seed_ratios))
            ]
            boot.append(statistics.median(sample))

        low = percentile(boot, 0.025)
        high = percentile(boot, 0.975)
        win_rate = sum(x > 1 for x in seed_ratios) / len(seed_ratios)

        setting = path.stem.rsplit("_", 1)[1]
        print(
            f"{setting:>4}%    {share:>7.2%}       "
            f"{n:<5} {degree:<7} {speedup:>7.3f}x       "
            f"[{low:.3f}, {high:.3f}]       {win_rate:.0%}"
        )

print("\nSpeedup > 1 favors support-count; < 1 favors legacy.")
print("Intervals are bootstrap estimates across seed-level results.")
