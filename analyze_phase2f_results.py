#!/usr/bin/env python3
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

raw_path = Path(
    sys.argv[1] if len(sys.argv) > 1
    else "results/phase2f_pruned_removal_raw.csv"
)
if not raw_path.exists():
    raise SystemExit(f"Raw results file not found: {raw_path}")

# Each sequence is uniquely identified by its graph, seed, repeat and method.
run_times = defaultdict(
    lambda: {"total": 0.0, "add": 0.0, "remove": 0.0,
             "adds": 0, "removes": 0}
)

with raw_path.open(newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        key = (
            int(row["n"]),
            int(row["degree"]),
            int(row["checkpoints"]),
            int(row["seed"]),
            int(row["repeat"]),
            row["method"],
        )
        elapsed = float(row["elapsed_ms"])
        operation = row["operation"]
        values = run_times[key]
        values["total"] += elapsed
        values[operation] += elapsed
        values["adds" if operation == "add" else "removes"] += 1

methods = (
    "full_recompute",
    "legacy_incremental",
    "pruned_removal",
    "support_count",
)

# Match results from the same graph/seed/repeat before calculating speedups.
paired_runs = defaultdict(dict)
for key, values in run_times.items():
    n, degree, cp, seed, repeat, method = key
    paired_runs[(n, degree, cp, seed, repeat)][method] = values

configs = sorted({key[:3] for key in run_times})
summary = []

print("\nMedian total time per complete update sequence")
print(
    "n     degree cp   full_ms  legacy_ms  pruned_ms  support_ms  "
    "pruned/legacy  pruned/full  pruned/support"
)

for config in configs:
    runs = [
        values for key, values in paired_runs.items()
        if key[:3] == config
    ]

    def median_metric(method, metric):
        vals = [
            run[method][metric]
            for run in runs
            if method in run
        ]
        return statistics.median(vals) if vals else float("nan")

    medians = {
        method: median_metric(method, "total")
        for method in methods
    }

    def paired_speedup(baseline, candidate):
        ratios = [
            run[baseline]["total"] / run[candidate]["total"]
            for run in runs
            if baseline in run and candidate in run
            and run[candidate]["total"] > 0
        ]
        return statistics.median(ratios) if ratios else float("nan")

    # Values > 1 mean the candidate is faster than the named baseline.
    speed_pruned_legacy = paired_speedup(
        "legacy_incremental", "pruned_removal"
    )
    speed_pruned_full = paired_speedup(
        "full_recompute", "pruned_removal"
    )
    speed_pruned_support = paired_speedup(
        "support_count", "pruned_removal"
    )

    n, degree, cp = config
    print(
        f"{n:<5} {degree:<6} {cp:<4} "
        f"{medians['full_recompute']:8.3f} "
        f"{medians['legacy_incremental']:10.3f} "
        f"{medians['pruned_removal']:10.3f} "
        f"{medians['support_count']:11.3f} "
        f"{speed_pruned_legacy:13.2f}x "
        f"{speed_pruned_full:11.2f}x "
        f"{speed_pruned_support:14.2f}x"
    )

    row = {"n": n, "degree": degree, "checkpoints": cp}
    for method in methods:
        row[f"{method}_total_median_ms"] = medians[method]
        row[f"{method}_add_median_ms"] = median_metric(method, "add")
        row[f"{method}_remove_median_ms"] = median_metric(method, "remove")

    row["pruned_speedup_vs_legacy"] = speed_pruned_legacy
    row["pruned_speedup_vs_full"] = speed_pruned_full
    row["pruned_speedup_vs_support_count"] = speed_pruned_support
    summary.append(row)

out_path = raw_path.with_name(raw_path.stem + "_total_summary.csv")
with out_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(summary[0]))
    writer.writeheader()
    writer.writerows(summary)

print("\nAddition/removal columns are medians of their per-sequence totals.")
print("Speedups are medians of paired baseline/candidate ratios.")
print("Summary saved to:", out_path)
