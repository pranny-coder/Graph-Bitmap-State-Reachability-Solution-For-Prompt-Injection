from phase2a_dynamic_updates import (
    make_unlabeled_graph,
    gbsr_apply_update,
    gbsr_apply_removal_pruned,
)
from phase2b_mixed_updates import (
    make_mixed_events,
    full_propagation,
)


def test_pruned_removal_matches_full_recomputation():
    for seed in range(10):
        graph = make_unlabeled_graph(
            n=100, degree=4, checkpoints=10, seed=seed
        )
        events = make_mixed_events(
            graph, operations=120, seed=9000 + seed
        )

        direct_s = [0] * graph.n
        direct_i = [0] * graph.n
        propagated_s = [0] * graph.n
        propagated_i = [0] * graph.n

        for step, (operation, kind, node, bit) in enumerate(events):
            if operation == "add":
                direct = direct_s if kind == "sensitive" else direct_i
                direct[node] |= bit
                gbsr_apply_update(
                    graph, propagated_s, propagated_i,
                    (kind, node, bit),
                )
            else:
                gbsr_apply_removal_pruned(
                    graph,
                    direct_s, direct_i,
                    propagated_s, propagated_i,
                    kind, node, bit,
                )

            expected_s, expected_i, _, _ = full_propagation(
                graph, direct_s, direct_i
            )

            assert propagated_s == expected_s, (
                f"Sensitive mismatch: seed={seed}, step={step}"
            )
            assert propagated_i == expected_i, (
                f"Untrusted mismatch: seed={seed}, step={step}"
            )
