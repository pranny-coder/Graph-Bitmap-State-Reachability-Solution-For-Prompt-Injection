"""Phase 2A supplement: dynamic source-label updates on a fixed DAG.

Unlike the static benchmark, this measures the workload GBSR is intended to help:
new sensitive/untrusted source labels arrive over time, and the system must refresh
output-checkpoint labels after each update. It compares:
  graph_traversal: recompute each checkpoint by walking its ancestors after every update;
  bitmap_recompute: recompute all node bitmaps in topological order after every update;
  gbsr_incremental: keep node bitmaps between updates and propagate only the newly
                    added source bit through affected descendants.

Source labels are known by construction: this measures algorithmic correctness and
cost, not an LLM's ability to infer semantic provenance from text.
"""
from __future__ import annotations
import argparse
import csv
import random
import statistics
import time
import tracemalloc
from collections import deque
from pathlib import Path
from typing import Any

from phase2a_scalability import RandomDAG, build_random_dag


def make_unlabeled_graph(n: int, degree: int, checkpoints: int, seed: int) -> RandomDAG:
    base = build_random_dag(n, degree, checkpoints, seed)
    return RandomDAG(
        n=base.n, parents=base.parents, children=base.children,
        direct_s=[0] * n, direct_i=[0] * n, checkpoints=base.checkpoints,
        seed=seed, avg_out_degree_requested=degree,
    )


def make_events(g: RandomDAG, source_events: int, seed: int):
    if source_events < 2:
        raise ValueError("source_events must be >= 2 to include sensitive and untrusted sources")
    if source_events > g.n:
        raise ValueError("source_events cannot exceed number of nodes")
    rng = random.Random(seed ^ 0x5A17)
    nodes = rng.sample(range(g.n), source_events)
    sensitive_count = (source_events + 1) // 2
    events = []
    sens_i = untrusted_i = 0
    for idx, node in enumerate(nodes):
        if idx < sensitive_count:
            events.append(("sensitive", node, 1 << sens_i))
            sens_i += 1
        else:
            events.append(("untrusted", node, 1 << untrusted_i))
            untrusted_i += 1
    return events


def traversal_snapshot(g: RandomDAG, direct_s: list[int], direct_i: list[int]):
    result = []
    nodes = edges = 0
    for sink in g.checkpoints:
        stack = [sink]
        # Mark when enqueued, so a node is pushed at most once per checkpoint.
        discovered: set[int] = {sink}
        s_mask = i_mask = 0
        while stack:
            v = stack.pop()
            s_mask |= direct_s[v]
            i_mask |= direct_i[v]
            nodes += 1
            for parent in g.parents[v]:
                edges += 1
                if parent not in discovered:
                    discovered.add(parent)
                    stack.append(parent)
        result.append((s_mask, i_mask))
    return tuple(result), nodes, edges


def bitmap_snapshot(g: RandomDAG, direct_s: list[int], direct_i: list[int]):
    s, inf = direct_s.copy(), direct_i.copy()
    edges = 0
    for parent in range(g.n):
        ps, pi = s[parent], inf[parent]
        for child in g.children[parent]:
            s[child] |= ps
            inf[child] |= pi
            edges += 1
    result = tuple((s[v], inf[v]) for v in g.checkpoints)
    return result, g.n, edges


def gbsr_apply_update(g: RandomDAG, propagated_s: list[int], propagated_i: list[int],
                      event: tuple[str, int, int]):
    kind, source_node, bit = event
    masks = propagated_s if kind == "sensitive" else propagated_i
    if masks[source_node] & bit:
        return 0, 0
    masks[source_node] |= bit
    q = deque([source_node])
    in_q = {source_node}
    popped = edges = 0
    while q:
        parent = q.popleft()
        in_q.discard(parent)
        popped += 1
        for child in g.children[parent]:
            edges += 1
            if not (masks[child] & bit):
                masks[child] |= bit
                if child not in in_q:
                    q.append(child)
                    in_q.add(child)
    return popped, edges


def run_sequence(g: RandomDAG, events, method: str):
    direct_s, direct_i = [0] * g.n, [0] * g.n
    propagated_s, propagated_i = [0] * g.n, [0] * g.n
    snapshots = []
    counters = {"nodes_examined": 0, "edges_examined": 0, "checkpoint_reads": 0,
                "source_updates": 0, "propagation_updates": 0}
    for event in events:
        kind, node, bit = event
        if kind == "sensitive":
            direct_s[node] |= bit
        else:
            direct_i[node] |= bit
        counters["source_updates"] += 1

        if method == "graph_traversal":
            snap, n_exam, e_exam = traversal_snapshot(g, direct_s, direct_i)
            counters["nodes_examined"] += n_exam
            counters["edges_examined"] += e_exam
        elif method == "bitmap_recompute":
            snap, n_exam, e_exam = bitmap_snapshot(g, direct_s, direct_i)
            counters["nodes_examined"] += n_exam
            counters["edges_examined"] += e_exam
        elif method == "gbsr_incremental":
            n_exam, e_exam = gbsr_apply_update(g, propagated_s, propagated_i, event)
            counters["nodes_examined"] += n_exam
            counters["edges_examined"] += e_exam
            counters["propagation_updates"] += n_exam
            snap = tuple((propagated_s[v], propagated_i[v]) for v in g.checkpoints)
        else:
            raise ValueError(f"unknown method: {method}")
        counters["checkpoint_reads"] += len(g.checkpoints)
        snapshots.append(snap)
    return tuple(snapshots), counters


def measure_method(g: RandomDAG, events, method: str, repeats: int, expected=None):
    # The graph-traversal reference is computed once per graph/update sequence and shared
    # across methods, so its cost does not unnecessarily inflate every baseline measurement.
    if expected is None:
        expected, _ = run_sequence(g, events, "graph_traversal")
    actual, counters = run_sequence(g, events, method)
    correct = actual == expected
    if not correct:
        raise AssertionError(f"{method} disagrees with graph traversal reference for seed={g.seed}")

    timings = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        outputs, _ = run_sequence(g, events, method)
        timings.append((time.perf_counter_ns() - start) / 1e6)
        if outputs != expected:
            raise AssertionError(f"{method} changed result across repetitions")

    tracemalloc.start()
    run_sequence(g, events, method)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "correct": correct,
        "median_total_ms": statistics.median(timings),
        "median_ms_per_update": statistics.median(timings) / len(events),
        "min_total_ms": min(timings),
        "max_total_ms": max(timings),
        "peak_python_bytes_excluding_graph": peak_bytes,
        **counters,
    }


def run_benchmark(sizes, degrees, checkpoint_counts, source_events, seeds, repeats, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    methods = ("graph_traversal", "bitmap_recompute", "gbsr_incremental")
    writer = None
    with out_path.open("w", newline="", encoding="utf-8") as f:
        for n in sizes:
            for degree in degrees:
                for checkpoints in checkpoint_counts:
                    for seed in seeds:
                        g = make_unlabeled_graph(n, degree, checkpoints, seed)
                        updates = min(source_events, n)
                        events = make_events(g, updates, seed)
                        reference_outputs, _ = run_sequence(g, events, "graph_traversal")
                        for method in methods:
                            measured = measure_method(g, events, method, repeats, expected=reference_outputs)
                            row = {
                                "n_nodes": n, "n_edges": g.m,
                                "requested_avg_out_degree": degree,
                                "actual_avg_out_degree": round(g.m / n, 4),
                                "requested_checkpoints": checkpoints,
                                "actual_checkpoints": len(g.checkpoints),
                                "source_events": updates, "seed": seed, "method": method,
                                "repeats": repeats, **measured,
                                "label_source": "known_by_construction_for_dynamic_algorithm_test_only",
                            }
                            if writer is None:
                                writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                                writer.writeheader()
                            writer.writerow(row)
                            f.flush()
                            rows.append(row)
                            print(f"n={n:>6} m={g.m:>7} deg={g.m/n:>5.2f} cp={len(g.checkpoints):>4} "
                                  f"updates={updates:>3} seed={seed:>3} {method:<20} "
                                  f"total={measured['median_total_ms']:>9.3f}ms "
                                  f"nodes={measured['nodes_examined']:>10} edges={measured['edges_examined']:>10} "
                                  f"correct={measured['correct']}", flush=True)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sizes", nargs="+", type=int, default=[1000, 5000])
    p.add_argument("--avg-out-degrees", nargs="+", type=int, default=[2, 8])
    p.add_argument("--checkpoints", nargs="+", type=int, default=[10, 100])
    p.add_argument("--source-events", type=int, default=8,
                   help="number of new labels introduced in sequence; must be >=2")
    p.add_argument("--seeds", nargs="+", type=int, default=[7, 17])
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--out", type=Path, default=Path("results/phase2a_dynamic_updates.csv"))
    args = p.parse_args()
    if args.repeats < 1:
        p.error("--repeats must be >= 1")
    print("Phase 2A supplement — dynamic label updates")
    print("Oracle labels are known by construction; this tests update mechanics, not LLM semantic attribution.")
    print(f"Sizes={args.sizes}; avg degree={args.avg_out_degrees}; checkpoints={args.checkpoints}; "
          f"source_events={args.source_events}; seeds={args.seeds}; repeats={args.repeats}")
    run_benchmark(args.sizes, args.avg_out_degrees, args.checkpoints,
                  args.source_events, args.seeds, args.repeats, args.out)
    print(f"\nWrote {args.out}")

if __name__ == "__main__":
    main()
