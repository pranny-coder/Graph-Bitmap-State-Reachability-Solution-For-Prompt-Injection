"""Phase 2A: randomized DAG scalability benchmark for graph/bitmap provenance.

This is a data-structure microbenchmark, NOT a prompt-injection security test.
Direct source labels are known by construction. It compares:
  graph_traversal: recompute labels by reverse ancestor traversal per checkpoint;
  bitmap:          full topological propagation, then O(1)-ish checks;
  gbsr:            worklist/delta bitmap propagation plus direct per-checkpoint checks.

Graph construction is outside timed/memory-measured regions. The graph is a DAG,
with edges directed from lower to higher node IDs. Metrics for node/edge visits are
algorithmic counters; method-specific work is not perfectly interchangeable.
"""
from __future__ import annotations
import argparse
import csv
import random
import statistics
import time
import tracemalloc
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SENSITIVE_BITS = 8
INFLUENCE_BITS = 8
FORBIDDEN_MASK = (1 << SENSITIVE_BITS) - 1


@dataclass
class RandomDAG:
    n: int
    parents: list[list[int]]
    children: list[list[int]]
    direct_s: list[int]
    direct_i: list[int]
    checkpoints: list[int]
    seed: int
    avg_out_degree_requested: int

    @property
    def m(self) -> int:
        return sum(len(x) for x in self.children)


def build_random_dag(n: int, avg_out_degree: int, checkpoints: int, seed: int) -> RandomDAG:
    if n < 2:
        raise ValueError("n must be at least 2")
    if avg_out_degree < 0:
        raise ValueError("avg_out_degree must be >= 0")
    if checkpoints < 1:
        raise ValueError("checkpoints must be >= 1")
    rng = random.Random(seed)
    parents = [[] for _ in range(n)]
    children = [[] for _ in range(n)]
    # Build a sparse DAG without an O(n^2) scan. The requested out-degree is
    # capped near the tail because edges always go from i to a larger ID.
    for i in range(n - 1):
        k = min(avg_out_degree, n - i - 1)
        if not k:
            continue
        targets = rng.sample(range(i + 1, n), k)
        for j in targets:
            children[i].append(j)
            parents[j].append(i)
    direct_s = [0] * n
    direct_i = [0] * n
    # Small source-label vocabularies make bitmask operations realistic and
    # create multiple sources of each class.
    for i in range(n):
        if rng.random() < 0.025:
            direct_s[i] |= 1 << rng.randrange(SENSITIVE_BITS)
        if rng.random() < 0.035:
            direct_i[i] |= 1 << rng.randrange(INFLUENCE_BITS)
    # Guarantee some source labels for every generated graph.
    if not any(direct_s):
        direct_s[0] = 1
    if not any(direct_i):
        direct_i[min(1, n - 1)] = 1
    checkpoints = min(checkpoints, n)
    sinks = sorted(rng.sample(range(n), checkpoints))
    return RandomDAG(n, parents, children, direct_s, direct_i, sinks, seed, avg_out_degree)


def traversal_queries(g: RandomDAG) -> tuple[dict[int, tuple[int, int]], dict[str, int]]:
    result: dict[int, tuple[int, int]] = {}
    nodes = edges = 0
    for sink in g.checkpoints:
        stack = [sink]
        # Mark on enqueue to prevent a dense DAG from accumulating duplicate
        # stack entries through many distinct paths to the same ancestor.
        discovered: set[int] = {sink}
        s_mask = i_mask = 0
        while stack:
            v = stack.pop()
            s_mask |= g.direct_s[v]
            i_mask |= g.direct_i[v]
            nodes += 1
            for parent in g.parents[v]:
                edges += 1
                if parent not in discovered:
                    discovered.add(parent)
                    stack.append(parent)
        result[sink] = (s_mask, i_mask)
    return result, {"nodes_examined": nodes, "edges_examined": edges,
                    "propagation_updates": 0, "trace_nodes_examined": 0,
                    "trace_edges_examined": 0, "conflicts": sum(bool(s & FORBIDDEN_MASK) for s, _ in result.values())}


def propagate_topological(g: RandomDAG) -> tuple[dict[int, tuple[int, int]], dict[str, int]]:
    s = g.direct_s.copy()
    inf = g.direct_i.copy()
    edges = 0
    # IDs are a topological order by construction.
    for parent in range(g.n):
        ps, pi = s[parent], inf[parent]
        for child in g.children[parent]:
            s[child] |= ps
            inf[child] |= pi
            edges += 1
    result = {v: (s[v], inf[v]) for v in g.checkpoints}
    return result, {"nodes_examined": g.n, "edges_examined": edges,
                    "propagation_updates": 0, "trace_nodes_examined": 0,
                    "trace_edges_examined": 0, "conflicts": sum(bool(sm & FORBIDDEN_MASK) for sm, _ in result.values())}


def propagate_gbsr(g: RandomDAG, include_trace: bool = False) -> tuple[dict[int, tuple[int, int]], dict[str, int]]:
    """Incremental delta propagation plus direct per-checkpoint bitmap checks.

    Since each output checkpoint has its own provenance bitmap, a graph walk is
    not needed to decide whether that component conflicts. An ancestor trace is
    useful for debugging/root-cause reporting or when the output is not split into
    independently tracked components, so it is an optional diagnostic ablation.
    """
    s = g.direct_s.copy()
    inf = g.direct_i.copy()
    # Pending masks contain only newly arrived bits, avoiding retransmitting the
    # complete accumulated mask when another source reaches the same node.
    pending_s = g.direct_s.copy()
    pending_i = g.direct_i.copy()
    queue = deque(i for i in range(g.n) if pending_s[i] or pending_i[i])
    in_queue = set(queue)
    popped = edges = updates = 0
    while queue:
        parent = queue.popleft()
        in_queue.discard(parent)
        popped += 1
        delta_s, delta_i = pending_s[parent], pending_i[parent]
        pending_s[parent] = pending_i[parent] = 0
        for child in g.children[parent]:
            edges += 1
            new_s = delta_s & ~s[child]
            new_i = delta_i & ~inf[child]
            if new_s or new_i:
                s[child] |= new_s
                inf[child] |= new_i
                pending_s[child] |= new_s
                pending_i[child] |= new_i
                updates += 1
                if child not in in_queue:
                    queue.append(child)
                    in_queue.add(child)
    result = {v: (s[v], inf[v]) for v in g.checkpoints}
    trace_nodes = trace_edges = conflicts = 0
    for sink in g.checkpoints:
        conflict = s[sink] & FORBIDDEN_MASK
        if not conflict:
            continue
        conflicts += 1
        if not include_trace:
            continue
        stack = [sink]
        seen: set[int] = set()
        while stack:
            v = stack.pop()
            if v in seen:
                continue
            seen.add(v)
            if not (s[v] & conflict):
                continue
            trace_nodes += 1
            for parent in g.parents[v]:
                trace_edges += 1
                if s[parent] & conflict:
                    stack.append(parent)
    return result, {"nodes_examined": popped, "edges_examined": edges,
                    "propagation_updates": updates, "trace_nodes_examined": trace_nodes,
                    "trace_edges_examined": trace_edges, "conflicts": conflicts}


def propagate_gbsr_with_trace(g: RandomDAG) -> tuple[dict[int, tuple[int, int]], dict[str, int]]:
    """Diagnostic ablation: run GBSR and trace every conflicting checkpoint."""
    return propagate_gbsr(g, include_trace=True)

METHODS: dict[str, Callable[[RandomDAG], tuple[dict[int, tuple[int, int]], dict[str, int]]]] = {
    "graph_traversal": traversal_queries,
    "bitmap": propagate_topological,
    "gbsr": propagate_gbsr,
}


def measure_one(fn: Callable, g: RandomDAG, repeats: int) -> dict:
    # Untimed warmup before gathering repeat timings.
    expected, counters = fn(g)
    # Validate all requested checkpoints against topological propagation.
    reference, _ = propagate_topological(g)
    correct = expected == reference
    if not correct:
        mismatches = [k for k in reference if reference[k] != expected.get(k)]
        raise AssertionError(f"Provenance mismatch at checkpoint(s): {mismatches[:10]}")
    timings_ms = []
    for _ in range(repeats):
        t0 = time.perf_counter_ns()
        out, _ = fn(g)
        elapsed_ms = (time.perf_counter_ns() - t0) / 1e6
        if out != reference:
            raise AssertionError("Method output changed between repeated runs")
        timings_ms.append(elapsed_ms)
    # Memory is measured separately because tracemalloc changes timings.
    tracemalloc.start()
    fn(g)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"correct": correct, "median_ms": statistics.median(timings_ms),
            "min_ms": min(timings_ms), "max_ms": max(timings_ms),
            "peak_python_bytes_excluding_graph": peak, **counters}


def run_benchmark(sizes: list[int], degrees: list[int], checkpoint_counts: list[int],
                  seeds: list[int], repeats: int, out_path: Path,
                  include_trace_ablation: bool = False) -> list[dict]:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    writer = None
    with out_path.open("w", newline="", encoding="utf-8") as f:
        for n in sizes:
            for degree in degrees:
                for checkpoint_count in checkpoint_counts:
                    for seed in seeds:
                        g = build_random_dag(n, degree, checkpoint_count, seed)
                        methods = list(METHODS.items())
                        if include_trace_ablation:
                            methods.append(("gbsr_with_trace", propagate_gbsr_with_trace))
                        for name, fn in methods:
                            measured = measure_one(fn, g, repeats)
                            row = {
                                "n_nodes": n, "n_edges": g.m,
                                "requested_avg_out_degree": degree,
                                "actual_avg_out_degree": round(g.m / n, 4),
                                "requested_checkpoints": checkpoint_count,
                                "actual_checkpoints": len(g.checkpoints),
                                "seed": seed, "method": name, "repeats": repeats,
                                **measured,
                                "label_source": "known_by_construction_for_algorithmic_correctness_only",
                            }
                            if writer is None:
                                writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                                writer.writeheader()
                            writer.writerow(row)
                            f.flush()  # Preserve completed measurements if a long run is interrupted.
                            rows.append(row)
                            print(f"n={n:>6} m={g.m:>7} avgdeg={g.m/n:>5.2f} "
                                  f"checkpoints={len(g.checkpoints):>5} seed={seed:>3} "
                                  f"{name:<16} median={measured['median_ms']:>9.3f}ms "
                                  f"nodes={measured['nodes_examined']:>9} "
                                  f"edges={measured['edges_examined']:>9} correct={measured['correct']}",
                                  flush=True)
    if not rows:
        raise ValueError("No measurements requested; check sizes/degrees/checkpoints/seeds")
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sizes", nargs="+", type=int, default=[1000, 5000, 10000])
    p.add_argument("--avg-out-degrees", nargs="+", type=int, default=[2, 8])
    p.add_argument("--checkpoints", nargs="+", type=int, default=[10, 100])
    p.add_argument("--seeds", nargs="+", type=int, default=[7, 17])
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--include-trace-ablation", action="store_true",
                   help="Include expensive GBSR + per-conflict graph tracing; use only for smaller graphs.")
    p.add_argument("--out", type=Path, default=Path("results/phase2a_scalability.csv"))
    args = p.parse_args()
    print("Phase 2A — scalability benchmark")
    print("Oracle/source labels are known by construction; this tests algorithm mechanics, not LLM attribution.")
    print(f"Sizes={args.sizes}; avg out-degree={args.avg_out_degrees}; checkpoints={args.checkpoints}; seeds={args.seeds}; repeats={args.repeats}")
    if args.include_trace_ablation and (max(args.sizes) > 5000 or max(args.checkpoints) > 100):
        p.error("--include-trace-ablation is intentionally limited to graphs <=5000 nodes and <=100 checkpoints")
    run_benchmark(args.sizes, args.avg_out_degrees, args.checkpoints, args.seeds,
                  args.repeats, args.out, include_trace_ablation=args.include_trace_ablation)
    print(f"\nWrote {args.out}")

if __name__ == "__main__":
    main()
