#!/usr/bin/env python3
"""Benchmark mixed source-label additions and removals on fixed DAGs.

This measures label-update correctness and computation cost with labels
known by construction. It does not evaluate LLM semantic attribution or
end-to-end prompt-injection defense.
"""

import argparse
import csv
import random
import statistics
import time
from pathlib import Path

from phase2a_dynamic_updates import (
    make_unlabeled_graph,
    gbsr_apply_update,
    gbsr_apply_removal,
)


def make_mixed_events(graph, operations, seed, remove_probability=0.40):
    """Create deterministic additions/removals, including shared label bits."""
    rng = random.Random(seed ^ 0x4D4958)
    active = set()
    events = []

    for _ in range(operations):
        if active and rng.random() < remove_probability:
            kind, node, bit = rng.choice(sorted(active))
            active.remove((kind, node, bit))
            events.append(("remove", kind, node, bit))
        else:
            while True:
                kind = rng.choice(("sensitive", "untrusted"))
                node = rng.randrange(graph.n)
                bit = 1 << rng.randrange(4)
                candidate = (kind, node, bit)
                if candidate not in active:
                    break

            active.add(candidate)
            events.append(("add", kind, node, bit))

    return events


def full_propagation(graph, direct_s, direct_i):
    """Independent full recomputation in topological order."""
    prop_s = direct_s.copy()
    prop_i = direct_i.copy()
    edges = 0

    for parent in range(graph.n):
        source_s = prop_s[parent]
        source_i = prop_i[parent]

        for child in graph.children[parent]:
            prop_s[child] |= source_s
            prop_i[child] |= source_i
            edges += 1

    return prop_s, prop_i, graph.n, edges


def checkpoint_snapshot(graph, prop_s, prop_i):
    return tuple(
        (prop_s[node], prop_i[node])
        for node in graph.checkpoints
    )


def run_full_recompute(graph, events):
    direct_s = [0] * graph.n
    direct_i = [0] * graph.n
    snapshots = []
    nodes_examined = 0
    edges_examined = 0

    for operation, kind, node, bit in events:
        direct = direct_s if kind == "sensitive" else direct_i

        if operation == "add":
            direct[node] |= bit
        else:
            direct[node] &= ~bit

        prop_s, prop_i, nodes, edges = full_propagation(
            graph, direct_s, direct_i
        )
        nodes_examined += nodes
        edges_examined += edges
        snapshots.append(checkpoint_snapshot(graph, prop_s, prop_i))

    return snapshots, {
        "nodes_examined": nodes_examined,
        "edges_examined": edges_examined,
    }


def run_gbsr_incremental(graph, events, validate_all_nodes=False):
    direct_s = [0] * graph.n
    direct_i = [0] * graph.n
    prop_s = [0] * graph.n
    prop_i = [0] * graph.n

    snapshots = []
    nodes_examined = 0
    edges_examined = 0

    for step, (operation, kind, node, bit) in enumerate(events):
        if operation == "add":
            direct = direct_s if kind == "sensitive" else direct_i
            direct[node] |= bit

            nodes, edges = gbsr_apply_update(
                graph, prop_s, prop_i, (kind, node, bit)
            )
        else:
            nodes, edges = gbsr_apply_removal(
                graph,
                direct_s,
                direct_i,
                prop_s,
                prop_i,
                kind,
                node,
                bit,
            )

        nodes_examined += nodes
        edges_examined += edges
        snapshots.append(checkpoint_snapshot(graph, prop_s, prop_i))

        # The complete reference comparison is run before timing, separately.
        if validate_all_nodes:
            ref_s, ref_i, _, _ = full_propagation(
                graph, direct_s, direct_i
            )
            if prop_s != ref_s or prop_i != ref_i:
                raise AssertionError(
                    f"Full-label mismatch after update {step}: "
                    f"operation={operation}, kind={kind}, node={node}, bit={bit}"
                )

    return snapshots, {
        "nodes_examined": nodes_examined,
        "edges_examined": edges_examined,
    }


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
        default=Path("results/phase2b_mixed_updates.csv"),
    )
    args = parser.parse_args()

    if args.operations < 1:
        parser.error("--operations must be >= 1")
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")

    rows = []

    print("Phase 2B — mixed source-label additions and removals")
    print("Labels are known by construction; no LLM inference is evaluated.")
    print(
        f"sizes={args.sizes}; degrees={args.avg_out_degrees}; "
        f"checkpoints={args.checkpoints}; operations={args.operations}; "
        f"seeds={args.seeds}; repeats={args.repeats}"
    )

    for n in args.sizes:
        for degree in args.avg_out_degrees:
            for checkpoint_count in args.checkpoints:
                if checkpoint_count > n:
                    raise ValueError(
                        f"checkpoint count {checkpoint_count} exceeds n={n}"
                    )

                for seed in args.seeds:
                    graph = make_unlabeled_graph(
                        n, degree, checkpoint_count, seed
                    )
                    edge_count = sum(len(c) for c in graph.children)
                    events = make_mixed_events(
                        graph, args.operations, seed
                    )

                    # Validate the complete propagated masks before timing.
                    expected, _ = run_full_recompute(graph, events)
                    checked, _ = run_gbsr_incremental(
                        graph, events, validate_all_nodes=True
                    )

                    if expected != checked:
                        raise AssertionError(
                            f"Checkpoint mismatch: n={n}, degree={degree}, "
                            f"checkpoints={checkpoint_count}, seed={seed}"
                        )

                    full_times = []
                    incremental_times = []
                    full_counters = None
                    incremental_counters = None

                    for repeat in range(args.repeats):
                        start = time.perf_counter_ns()
                        full_snapshots, full_counters = run_full_recompute(
                            graph, events
                        )
                        full_ms = (time.perf_counter_ns() - start) / 1e6

                        start = time.perf_counter_ns()
                        incremental_snapshots, incremental_counters = (
                            run_gbsr_incremental(graph, events)
                        )
                        incremental_ms = (
                            time.perf_counter_ns() - start
                        ) / 1e6

                        correct = full_snapshots == incremental_snapshots
                        if not correct:
                            raise AssertionError(
                                f"Checkpoint mismatch during timing: "
                                f"n={n}, degree={degree}, seed={seed}, "
                                f"repeat={repeat}"
                            )

                        full_times.append(full_ms)
                        incremental_times.append(incremental_ms)

                        common = {
                            "n": n,
                            "m": edge_count,
                            "requested_degree": degree,
                            "actual_average_degree": edge_count / n,
                            "checkpoints": checkpoint_count,
                            "operations": args.operations,
                            "seed": seed,
                            "repeat": repeat + 1,
                            "correct": correct,
                        }

                        rows.append({
                            **common,
                            "method": "full_recompute",
                            "total_ms": full_ms,
                            **full_counters,
                        })
                        rows.append({
                            **common,
                            "method": "gbsr_incremental",
                            "total_ms": incremental_ms,
                            **incremental_counters,
                        })

                    paired_speedups = [
                        full / inc
                        for full, inc in zip(full_times, incremental_times)
                    ]
                    print(
                        f"n={n:5} m={edge_count:7} degree={degree} "
                        f"cp={checkpoint_count:3} seed={seed:3} "
                        f"correct=True "
                        f"full_median={statistics.median(full_times):9.3f}ms "
                        f"incremental_median="
                        f"{statistics.median(incremental_times):9.3f}ms "
                        f"paired_speedup_median="
                        f"{statistics.median(paired_speedups):7.2f}x "
                        f"nodes={incremental_counters['nodes_examined']} "
                        f"edges={incremental_counters['edges_examined']}"
                    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {args.out}")
    print(f"CSV rows: {len(rows)}")


if __name__ == "__main__":
    main()
