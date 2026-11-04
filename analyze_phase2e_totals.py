import csv
import statistics
from collections import defaultdict
from pathlib import Path

files = {
    10: Path("results/phase2e_removal10.csv"),
    40: Path("results/phase2e_removal40.csv"),
    70: Path("results/phase2e_removal70.csv"),
}

# Accumulate total time for each complete update sequence.
totals = defaultdict(float)
event_counts = defaultdict(int)

for probability, path in files.items():
    if not path.exists():
        raise SystemExit(f"Missing file: {path}")

    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (
                probability,
                int(row["n"]),
                int(row["degree"]),
                int(row["checkpoints"]),
                int(row["seed"]),
                int(row["repeat"]),
                row["method"],
            )
            totals[key] += float(row["elapsed_ms"])
            event_counts[key] += 1

configs = sorted({
    key[:4] for key in totals
})

print(
    "removal%  n      degree cp  "
    "full_ms  legacy_ms support_ms  "
    "support_speedup_vs_legacy"
)

summary = []

for config in configs:
    probability, n, degree, checkpoints = config

    matched = []
    for seed in (1, 2, 3):
        for repeat in (1, 2, 3):
            base = (probability, n, degree, checkpoints, seed, repeat)
            keys = {
                method: base + (method,)
                for method in (
                    "full_recompute",
                    "legacy_incremental",
                    "support_count",
                )
            }

            if not all(key in totals for key in keys.values()):
                continue

            # The full methods must process the same event sequence.
            counts = [event_counts[key] for key in keys.values()]
            if len(set(counts)) != 1:
                raise AssertionError(
                    f"Event counts differ for configuration {base}: {counts}"
                )

            full = totals[keys["full_recompute"]]
            legacy = totals[keys["legacy_incremental"]]
            support = totals[keys["support_count"]]

            matched.append({
                "full": full,
                "legacy": legacy,
                "support": support,
                "support_vs_legacy": legacy / support,
                "legacy_vs_support": support / legacy,
                "event_count": counts[0],
            })

    if not matched:
        continue

    full = statistics.median(x["full"] for x in matched)
    legacy = statistics.median(x["legacy"] for x in matched)
    support = statistics.median(x["support"] for x in matched)
    speedup = statistics.median(
        x["support_vs_legacy"] for x in matched
    )
    events = statistics.median(x["event_count"] for x in matched)

    print(
        f"{probability:<9} {n:<6} {degree:<6} {checkpoints:<3} "
        f"{full:7.2f} {legacy:10.2f} {support:10.2f} "
        f"{speedup:10.2f}x"
    )

    summary.append({
        "removal_probability_pct": probability,
        "n": n,
        "degree": degree,
        "checkpoints": checkpoints,
        "median_events": events,
        "full_recompute_median_ms": full,
        "legacy_incremental_median_ms": legacy,
        "support_count_median_ms": support,
        "support_count_speedup_vs_legacy": speedup,
        "matched_sequences": len(matched),
    })

out = Path("results/phase2e_total_sequence_summary.csv")
with out.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
    writer.writeheader()
    writer.writerows(summary)

print(f"\nWrote {out}")
