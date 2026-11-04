import random

from phase2a_dynamic_updates import make_unlabeled_graph
from phase2b_mixed_updates import (
    make_mixed_events,
    full_propagation,
)
from phase2d_support_counts import SupportCountPropagator


def test_support_counts_match_reference_on_random_mixed_updates():
    for seed in range(10):
        graph = make_unlabeled_graph(
            n=80, degree=3, checkpoints=8, seed=seed
        )
        events = make_mixed_events(
            graph, operations=100, seed=7000 + seed
        )
        algorithm = SupportCountPropagator(graph)

        for step, (operation, kind, node, bit) in enumerate(events):
            algorithm.apply(operation, kind, node, bit)

            expected_s, expected_i, _, _ = full_propagation(
                graph,
                algorithm.direct_s,
                algorithm.direct_i,
            )

            assert algorithm.propagated_s == expected_s, (
                f"sensitive mismatch: seed={seed}, step={step}"
            )
            assert algorithm.propagated_i == expected_i, (
                f"untrusted mismatch: seed={seed}, step={step}"
            )


def test_removal_preserves_shared_parent_support():
    graph = make_unlabeled_graph(
        n=100, degree=4, checkpoints=10, seed=123
    )

    # Find a child that has at least two parents.
    child = next(
        i for i, parents in enumerate(graph.parents)
        if len(parents) >= 2
    )
    parent_a, parent_b = graph.parents[child][:2]
    bit = 1

    algorithm = SupportCountPropagator(graph)
    algorithm.apply("add", "sensitive", parent_a, bit)
    algorithm.apply("add", "sensitive", parent_b, bit)

    # Only remove parent_a's direct source.
    algorithm.apply("remove", "sensitive", parent_a, bit)

    expected_s, expected_i, _, _ = full_propagation(
        graph, algorithm.direct_s, algorithm.direct_i
    )

    assert algorithm.propagated_s == expected_s
    assert algorithm.propagated_i == expected_i

    # Parent B remains a support for its descendants.
    assert algorithm.propagated_s[parent_b] & bit
    assert algorithm.propagated_s[child] & bit
