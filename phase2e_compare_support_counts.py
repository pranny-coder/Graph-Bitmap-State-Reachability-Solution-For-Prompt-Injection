#!/usr/bin/env python3
"""Compare full recomputation, legacy incremental, and support-count updates."""

import argparse
import csv
import statistics
import time
from collections import defaultdict
from pathlib import Path

from phase2a_dynamic_updates import (
    make_unlabeled_graph,
    gbsr_apply_update,
    gbsr_apply_removal,
    gbsr_apply_removal_pruned,
)
from phase2b_mixed_updates import make_mixed_events, full_propagation
from phase2d_support_counts import SupportCountPropagator


def snapshot(s, i):
    return tuple(zip(s, i))


def run_method(graph, events, method):
    direct_s = [0] * graph.n
    direct_i = [0] * graph.n
    prop_s = [0] * graph.n
    prop_i = [0] * graph.n

    support = SupportCountPropagator(graph) if method == "support_count" else None
    history = []
    measurements = []

    edge_count = sum(len(children) for children in graph.children)

    for step, (operation, kind, node, bit) in enumerate(events):
        started = time.perf_counter_ns()
        edges_examined = edge_count

        if method == "full_recompute":
            direct = direct_s if kind == "sensitive" else direct_i
            if operation == "add":
                direct[node] |= bit
            else:
                direct[node] &= ~bit

            prop_s, prop_i, _, _ = full_propagation(
                graph, direct_s, direct_i
            )

        elif method == "legacy_incremental":
            if operation == "add":
                direct = direct_s if kind == "sensitive" else direct_i
                direct[node] |= bit
                _, edges_examined = gbsr_apply_update(
                    graph, prop_s, prop_i, (kind, node, bit)
                )
            else:
                _, edges_examined = gbsr_apply_removal(
                    graph,
                    direct_s,
                    direct_i,
                    prop_s,
                    prop_i,
                    kind,
                    node,
                    bit,
                )

        elif method == "pruned_removal":
            if operation == "add":
                direct = direct_s if kind == "sensitive" else direct_i
                direct[node] |= bit
                _, edges_examined = gbsr_apply_update(
                    graph, prop_s, prop_i, (kind, node, bit)
                )
            else:
                _, edges_examined = gbsr_apply_removal_pruned(
                    graph,
                    direct_s,
                    direct_i,
                    prop_s,
                    prop_i,
                    kind,
                    node,
                    bit,
                )

        elif method == "support_count":
            _, edges_examined = support.apply(
                operation, kind, node, bit
            )
            direct_s = support.direct_s
            direct_i = support.direct_i
            prop_s = support.propagated_s
            prop_i = support.propagated_i

        else:
            raise ValueError(f"Unknown method: {method}")

        elapsed_ms = (time.perf_counter_ns() - started) / 1e6

        measurements.append({
            "step": step,
            "operation": operation,
            "label_kind": kind,
            "source_node": node,
            "bit": bit,
            "method": method,
            "elapsed_ms": elapsed_ms,
            "edges_examined": edges_examined,
        })

        # Keep full state snapshots outside the timed region.
        history.append((tuple(prop_s), tuple(prop_i)))

    return history, measurements


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 5000])
    parser.add_argument(
        "--avg-out-degrees", nargs="+", type=int, default=[2, 8]
    )
    parser.add_argument(
        "--checkpoints", nargs="+", type=int, default=[10, 100]
    )
    parser.add_argument("--operations", type=int, default=40)
    parser.add_argument("--remove-probability", type=float, default=0.40)
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--out", type=Path,
        default=Path("results/phase2e_support_counts_raw.csv"),
    )
    args = parser.parse_args()

    if not 0.0 <= args.remove_probability <= 1.0:
        parser.error("--remove-probability must be between 0 and 1")
    if args.operations < 1 or args.repeats < 1:
        parser.error("--operations and --repeats must be >= 1")

    raw_rows = []
    # Total time for each operation category, graph, seed, repeat, and method.
    totals = defaultdict(float)
    edge_totals = defaultdict(int)
    methods = (
        "full_recompute",
        "legacy_incremental",
        "pruned_removal",
        "support_count",
    )

    print("Phase 2E — support-count comparison")
    print("Source labels are known by construction; no LLM inference is tested.")

    for n in args.sizes:
        for degree in args.avg_out_degrees:
            for checkpoints in args.checkpoints:
                if checkpoints > n:
                    parser.error(
                        f"checkpoints={checkpoints} exceeds n={n}"
                    )

                for seed in args.seeds:
                    graph = make_unlabeled_graph(
                        n, degree, checkpoints, seed
                    )
                    events = make_mixed_events(graph, args.operations, seed, args.remove_probability)
                    expected_history = None

                    for repeat in range(1, args.repeats + 1):
                        # Alternate order to reduce systematic timing-order bias.
                        order = list(methods)
                        if repeat % 2 == 0:
                            order.reverse()

                        result = {}
                        for method in order:
                            history, measurements = run_method(
                                graph, events, method
                            )
                            result[method] = (history, measurements)

                        reference = result["full_recompute"][0]

                        for method in methods:
                            history, measurements = result[method]
                            if history != reference:
                                raise AssertionError(
                                    f"Label mismatch: n={n}, degree={degree}, "
                                    f"checkpoints={checkpoints}, seed={seed}, "
                                    f"repeat={repeat}, method={method}"
                                )

                            for record in measurements:
                                common = {
                                    "n": n,
                                    "edges": sum(
                                        len(children) for children in graph.children
                                    ),
                                    "degree": degree,
                                    "checkpoints": checkpoints,
                                    "seed": seed,
                                    "repeat": repeat,
                                }
                                raw_rows.append({**common, **record})

                                key = (
                                    n, degree, checkpoints, seed, repeat,
                                    record["operation"], method,
                                )
                                totals[key] += record["elapsed_ms"]
                                edge_totals[key] += record["edges_examined"]

                    print(
                        f"n={n} degree={degree} checkpoints={checkpoints} "
                        f"seed={seed}: all methods correct"
                    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(raw_rows[0]))
        writer.writeheader()
        writer.writerows(raw_rows)

    # Report medians across matched seeds and repetitions.
    groups = sorted({
        (key[0], key[1], key[2], key[5])
        for key in totals
    })

    summary_rows = []
    print("\nMedian total time per operation type")
    print(
        "n    degree cp  operation  full_ms  legacy_ms  support_ms  "
        "support/full  support/legacy"
    )

    for n, degree, checkpoints, operation in groups:
        full_values = []
        legacy_values = []
        support_values = []
        support_full_ratios = []
        support_legacy_ratios = []

        for seed in args.seeds:
            for repeat in range(1, args.repeats + 1):
                common = (n, degree, checkpoints, seed, repeat, operation)
                full = totals[common + ("full_recompute",)]
                legacy = totals[common + ("legacy_incremental",)]
                support = totals[common + ("support_count",)]

                full_values.append(full)
                legacy_values.append(legacy)
                support_values.append(support)

                if support > 0:
                    support_full_ratios.append(full / support)
                    if legacy > 0:
                        support_legacy_ratios.append(legacy / support)

        full_med = statistics.median(full_values)
        legacy_med = statistics.median(legacy_values)
        support_med = statistics.median(support_values)
        sf = statistics.median(support_full_ratios)
        sl = statistics.median(support_legacy_ratios)

        print(
            f"{n:<4} {degree:<6} {checkpoints:<3} {operation:<10} "
            f"{full_med:7.3f} {legacy_med:10.3f} {support_med:11.3f} "
            f"{sf:12.2f}x {sl:14.2f}x"
        )

        summary_rows.append({
            "n": n,
            "degree": degree,
            "checkpoints": checkpoints,
            "operation": operation,
            "full_median_ms": full_med,
            "legacy_incremental_median_ms": legacy_med,
            "support_count_median_ms": support_med,
            "support_count_speedup_vs_full": sf,
            "support_count_speedup_vs_legacy": sl,
        })

    summary_path = args.out.with_name(
        args.out.stem + "_summary.csv"
    )
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(summary_rows[0])
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    print("\nAll label comparisons passed.")
    print("Raw measurements:", args.out)
    print("Summary:", summary_path)
    print("Raw rows:", len(raw_rows))


if __name__ == "__main__":
    main()
