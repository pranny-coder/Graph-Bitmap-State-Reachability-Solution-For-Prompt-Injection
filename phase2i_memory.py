import csv
import gc
import statistics
import tracemalloc
from collections import defaultdict
from pathlib import Path

from phase2a_dynamic_updates import (
    make_unlabeled_graph,
    gbsr_apply_update,
    gbsr_apply_removal,
)
from phase2b_mixed_updates import make_mixed_events, full_propagation
from phase2d_support_counts import SupportCountPropagator


def run_legacy(graph, events):
    ds = [0] * graph.n
    di = [0] * graph.n
    ps = [0] * graph.n
    pi = [0] * graph.n

    for op, kind, node, bit in events:
        if op == "add":
            direct = ds if kind == "sensitive" else di
            direct[node] |= bit
            gbsr_apply_update(graph, ps, pi, (kind, node, bit))
        else:
            gbsr_apply_removal(
                graph, ds, di, ps, pi, kind, node, bit
            )

    return ds, di, ps, pi


def run_support(graph, events):
    state = SupportCountPropagator(graph)
    for op, kind, node, bit in events:
        state.apply(op, kind, node, bit)
    return state


def measure(method, graph, events):
    gc.collect()
    tracemalloc.start()
    try:
        state = (
            run_legacy(graph, events)
            if method == "legacy_incremental"
            else run_support(graph, events)
        )
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    if method == "legacy_incremental":
        ds, di, ps, pi = state
    else:
        ds, di = state.direct_s, state.direct_i
        ps, pi = state.propagated_s, state.propagated_i

    expected_s, expected_i, _, _ = full_propagation(graph, ds, di)
    if ps != expected_s or pi != expected_i:
        raise AssertionError(
            f"Correctness failure: {method}"
        )

    del state
    return current / (1024 ** 2), peak / (1024 ** 2)


def main():
    output = Path("results/phase2i_memory_raw.csv")
    output.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for n in (1000, 5000):
        for degree in (2, 8):
            for probability in (0.35, 0.55, 0.70):
                for seed in (211, 212, 213, 214, 215):
                    graph = make_unlabeled_graph(
                        n, degree, checkpoints=10, seed=seed
                    )
                    events = make_mixed_events(
                        graph, operations=80, seed=seed,
                        remove_probability=probability
                    )
                    actual_share = sum(
                        op == "remove" for op, *_ in events
                    ) / len(events)

                    for method in (
                        "legacy_incremental", "support_count"
                    ):
                        current_mib, peak_mib = measure(
                            method, graph, events
                        )
                        rows.append({
                            "n": n,
                            "degree": degree,
                            "remove_probability": probability,
                            "actual_removal_share": actual_share,
                            "seed": seed,
                            "method": method,
                            "state_current_mib": current_mib,
                            "state_peak_mib": peak_mib,
                        })
                        print(
                            f"n={n} d={degree} p={probability:.2f} "
                            f"seed={seed} {method}: "
                            f"current={current_mib:.3f} MiB, "
                            f"peak={peak_mib:.3f} MiB; labels correct"
                        )

    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    groups = defaultdict(list)
    for row in rows:
        key = (
            row["n"], row["degree"], row["remove_probability"],
            row["method"]
        )
        groups[key].append(row)

    print("\nMedian propagation-state memory across seeds")
    print("n    degree setting method               current MiB  peak MiB")
    for key in sorted(groups):
        n, degree, p, method = key
        data = groups[key]
        cur = statistics.median(
            row["state_current_mib"] for row in data
        )
        peak = statistics.median(
            row["state_peak_mib"] for row in data
        )
        print(
            f"{n:<4} {degree:<6} {p:<7.2f} "
            f"{method:<21} {cur:>9.3f} {peak:>9.3f}"
        )

    print("\nRaw results:", output)


if __name__ == "__main__":
    main()
