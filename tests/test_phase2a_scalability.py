import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phase2a_scalability import (  # noqa: E402
    build_random_dag, traversal_queries, propagate_topological, propagate_gbsr,
)


def test_all_methods_match_on_small_seeded_dags():
    for seed in (1, 7, 42):
        graph = build_random_dag(n=80, avg_out_degree=5, checkpoints=25, seed=seed)
        reference, _ = traversal_queries(graph)
        topological, _ = propagate_topological(graph)
        gbsr, _ = propagate_gbsr(graph)
        assert reference == topological == gbsr


def test_zero_edge_graph_still_propagates_direct_labels():
    graph = build_random_dag(n=20, avg_out_degree=0, checkpoints=20, seed=3)
    a, _ = traversal_queries(graph)
    b, _ = propagate_topological(graph)
    c, _ = propagate_gbsr(graph)
    assert a == b == c


def test_checkpoints_are_capped_at_node_count():
    graph = build_random_dag(n=12, avg_out_degree=2, checkpoints=100, seed=5)
    assert len(graph.checkpoints) == 12
