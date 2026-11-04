import csv
import statistics
from collections import defaultdict
from pathlib import Path

files = {
    10: Path("results/phase2e_removal10.csv"),
    40: Path("results/phase2e_removal40.csv"),
    70: Path("results/phase2e_removal70.csv"),
}

# Aggregate each operation sequence by graph, seed, repetition and operation.
groups = defaultdict(lambda: defaultdict(list))

for probability, path in files.items():
    if not path.exists():
        raise SystemExit(f"Missing results file: {path}")

    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (
                probability,
                int(row["n"]),
                int(row["degree"]),
                int(row["checkpoints"]),
                int(row["seed"]),
                int(row["repeat"]),
                row["operation"],
            )
            groups[key][row["method"]].append(float(row["elapsed_ms"]))

# Convert each matched sequence to mean latency per event and paired speedups.
sequences = defaultdict(list)
counts = defaultdict(list)

for key, method_values in groups.items():
    probability, n, degree, checkpoints, seed, repeat, operation = key
    required = {"full_recompute", "legacy_incremental", "support_count"}

    if not required.issubset(method_values):
        continue

    event_counts = {len(method_values[m]) for m in required}
    if len(event_counts) != 1:
        raise AssertionError(f"Method event counts differ for {key}")

    count = event_counts.pop()
    if count == 0:
        continue

    means = {
        method: statistics.mean(values)
        for method, values in method_values.items()
    }

    summary_key = (probability, n, degree, operation)
    sequences[summary_key].append({
        "count": count,
        "full": means["full_recompute"],
        "legacy": means["legacy_incremental"],
        "support": means["support_count"],
        "support_vs_full": means["full_recompute"] / means["support_count"],
        "support_vs_legacy": (
            means["legacy_incremental"] / means["support_count"]
        ),
    })
    counts[(probability, n, degree, operation)].append(count)

print("Median per-event latency and paired speedups")
print("prob%  n      degree  operation  count[min-max]  full_ms  legacy_ms  support_ms  support/full  support/legacy")

summary_rows = []

for key in sorted(sequences):
    probability, n, degree, operation = key
    records = sequences[key]
    event_counts = [r["count"] for r in records]

    full = statistics.median(r["full"] for r in records)
    legacy = statistics.median(r["legacy"] for r in records)
    support = statistics.median(r["support"] for r in records)
    sf = statistics.median(r["support_vs_full"] for r in records)
    sl = statistics.median(r["support_vs_legacy"] for r in records)

    print(
        f"{probability:<6} {n:<6} {degree:<7} {operation:<10} "
        f"{min(event_counts)}-{max(event_counts):<12} "
        f"{full:7.4f} {legacy:10.4f} {support:11.4f} "
        f"{sf:12.2f}x {sl:14.2f}x"
    )

    summary_rows.append({
        "removal_probability_pct": probability,
        "n": n,
        "degree": degree,
        "operation": operation,
        "min_events_per_sequence": min(event_counts),
        "max_events_per_sequence": max(event_counts),
        "full_median_ms_per_event": full,
        "legacy_median_ms_per_event": legacy,
        "support_median_ms_per_event": support,
        "support_speedup_vs_full": sf,
        "support_speedup_vs_legacy": sl,
        "matched_sequences": len(records),
    })

out = Path("results/phase2e_normalized_summary.csv")
out.parent.mkdir(parents=True, exist_ok=True)

with out.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0]))
    writer.writeheader()
    writer.writerows(summary_rows)

print(f"\nWrote {out}")
