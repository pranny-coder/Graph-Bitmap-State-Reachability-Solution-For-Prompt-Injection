import csv
import statistics
from collections import Counter, defaultdict
from pathlib import Path

files = [
    p for p in sorted(Path("results").glob("phase2g_validation_*.csv"))
    if not p.stem.endswith("_summary")
]

if not files:
    raise SystemExit("No raw phase2g_validation CSV files found.")

for path in files:
    per_sequence = defaultdict(Counter)
    total = Counter()

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames or "method" not in reader.fieldnames:
            print(f"Skipping non-raw file: {path.name}")
            continue

        for row in reader:
            # Count each generated event exactly once.
            if row["method"] != "full_recompute":
                continue

            sequence = (
                int(row["n"]),
                int(row["degree"]),
                int(row["checkpoints"]),
                int(row["seed"]),
                int(row["repeat"]),
            )
            operation = row["operation"]
            per_sequence[sequence][operation] += 1
            total[operation] += 1

    adds = total["add"]
    removes = total["remove"]
    count = adds + removes

    print(f"\n=== {path.name} ===")
    print(
        f"Events: {count}; additions: {adds}; removals: {removes}; "
        f"actual removal share: {removes / count:.2%}"
        if count else "No events found."
    )

    print(
        "n     degree   median adds   median removes   "
        "removal-share range"
    )

    for n, degree in sorted({
        (key[0], key[1]) for key in per_sequence
    }):
        rows = [
            counts for key, counts in per_sequence.items()
            if key[0] == n and key[1] == degree
        ]

        add_counts = [r["add"] for r in rows]
        remove_counts = [r["remove"] for r in rows]

        shares = [
            r["remove"] / (r["add"] + r["remove"])
            for r in rows if r["add"] + r["remove"]
        ]

        print(
            f"{n:<5} {degree:<8} "
            f"{statistics.median(add_counts):11.1f} "
            f"{statistics.median(remove_counts):15.1f} "
            f"{min(shares):.1%}–{max(shares):.1%}"
        )
