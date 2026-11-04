#!/usr/bin/env python3
"""Profile mixed source-label additions and removals independently."""

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
)
from phase2b_mixed_updates import make_mixed_events, full_propagation


def checkpoint_snapshot(graph, prop_s, prop_i):
    return tuple(
        (prop_s[node], prop_i[node])
        for node in graph.checkpoints
    )


def run_method(graph, events, method):
    direct_s = [0] * graph.n
    direct_i = [0] * graph.n
    prop_s = [0] * graph.n
    prop_i = [0] * graph.n

    records = []
    snapshots = []
    edge_count = sum(len(children) for children in graph.children)

    for step, (operation, kind, node, bit) in enumerate(events):
        started = time.perf_counter_ns()

        if method == "full_recompute":
            direct = direct_s if kind == "sensitive" else direct_i

            if operation == "add":
                direct[node] |= bit
            else:
                direct[node] &= ~bit

            prop_s, prop_i, nodes_examined, edges_examined = (
                full_propagation(graph, direct_s, direct_i)
            )

        elif method == "gbsr_incremental":
            if operation == "add":
                direct = direct_s if kind == "sensitive" else direct_i
                direct[node] |= bit
                nodes_examined, edges_examined = gbsr_apply_update(
                    graph, prop_s, prop_i, (kind, node, bit)
                )
            else:
                nodes_examined, edges_examined = gbsr_apply_removal(
                    graph,
                    direct_s,
                    direct_i,
                    prop_s,
                    prop_i,
                    kind,
                    node,
                    bit,
                )
        else:
            raise ValueError(f"Unknown method: {method}")

        elapsed_ms = (time.perf_counter_ns() - started) / 1e6

        # Verify the full node labels outside the timed section.
        ref_s, ref_i, _, _ = full_propagation(
            graph, direct_s, direct_i
        )
        correct = prop_s == ref_s and prop_i == ref_i

        if not correct:
            raise AssertionError(
                f"Label mismatch: method={method}, step={step}, "
                f"operation={operation}, kind={kind}, node={node}, bit={bit}"
            )

        snapshots.append(checkpoint_snapshot(graph, prop_s, prop_i))

        records.append({
            "step": step,
            "operation": operation,
            "label_kind": kind,
            "source_node": node,
            "bit": bit,
            "method": method,
            "elapsed_ms": elapsed_ms,
            "nodes_examined": nodes_examined,
            "edges_examined": edges_examined,
            "correct": correct,
        })

    return records, snapshots


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
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("results/phase2c_operation_profile.csv"),
    )
    args = parser.parse_args()

    if args.operations < 1 or args.repeats < 1:
        parser.error("--operations and --repeats must be >= 1")

    rows = []
    paired_ratios = defaultdict(list)
    timing_by_group = defaultdict(lambda: {
        "full": [], "incremental": []
    })

    print("Phase 2C — per-operation profiling")
    print("Labels are known by construction; no LLM inference is evaluated.")

    for n in args.sizes:
        for degree in args.avg_out_degrees:
            for checkpoints in args.checkpoints:
                if checkpoints > n:
                    parser.error(
                        f"checkpoints={checkpoints} exceeds graph size n={n}"
                    )

                for seed in args.seeds:
                    graph = make_unlabeled_graph(
                        n, degree, checkpoints, seed
                    )
                    events = make_mixed_events(graph, args.operations, seed)
                    counts = defaultdict(int)

                    for operation, _, _, _ in events:
                        counts[operation] += 1

                    for repeat in range(1, args.repeats + 1):
                        # Alternate order to reduce systematic timing bias.
                        methods = (
                            ["full_recompute", "gbsr_incremental"]
                            if repeat % 2 else
                            ["gbsr_incremental", "full_recompute"]
                        )

                        result = {}
                        for method in methods:
                            records, snapshots = run_method(
                                graph, events, method
                            )
                            result[method] = (records, snapshots)

                            for record in records:
                                rows.append({
                                    "n": n,
                                    "edges": sum(
                                        len(c) for c in graph.children
                                    ),
                                    "requested_degree": degree,
                                    "checkpoints": checkpoints,
                                    "seed": seed,
                                    "repeat": repeat,
                                    **record,
                                })

                        full_records, full_snapshots = result["full_recompute"]
                        inc_records, inc_snapshots = result["gbsr_incremental"]

                        if full_snapshots != inc_snapshots:
                            raise AssertionError(
                                f"Checkpoint mismatch for n={n}, seed={seed}"
                            )

                        for full, inc in zip(full_records, inc_records):
                            assert full["step"] == inc["step"]
                            assert full["operation"] == inc["operation"]

                            operation = full["operation"]
                            group = (n, degree, checkpoints, operation)

                            ratio = (
                                full["elapsed_ms"] / inc["elapsed_ms"]
                                if inc["elapsed_ms"] > 0 else float("inf")
                            )
                            paired_ratios[group].append(ratio)
                            timing_by_group[group]["full"].append(
                                full["elapsed_ms"]
                            )
                            timing_by_group[group]["incremental"].append(
                                inc["elapsed_ms"]
                            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print("\nMedian paired speedup by operation")
    print("n      degree   checkpoints   operation    speedup   full_ms   inc_ms")

    for group in sorted(paired_ratios):
        n, degree, checkpoints, operation = group
        speedup = statistics.median(paired_ratios[group])
        full_ms = statistics.median(timing_by_group[group]["full"])
        inc_ms = statistics.median(timing_by_group[group]["incremental"])

        print(
            f"{n:<6}  {degree:<7}  {checkpoints:<12} "
            f"{operation:<12} {speedup:>7.2f}x "
            f"{full_ms:>8.3f} {inc_ms:>8.3f}"
        )

    print("\nOperation counts:", dict(counts))
    print(f"All label comparisons passed.")
    print(f"Wrote {args.out}")
    print(f"CSV rows: {len(rows)}")


if __name__ == "__main__":
    main()
