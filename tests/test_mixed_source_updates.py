import random

from phase2a_dynamic_updates import (
    make_unlabeled_graph,
    gbsr_apply_update,
    gbsr_apply_removal,
)


def reference_propagation(graph, direct_s, direct_i):
    """Independent full recomputation in topological node order."""
    result_s = list(direct_s)
    result_i = list(direct_i)

    for parent in range(graph.n):
        for child in graph.children[parent]:
            result_s[child] |= result_s[parent]
            result_i[child] |= result_i[parent]

    return result_s, result_i


def test_randomized_mixed_add_remove_sequences_match_reference():
    for seed in range(10):
        rng = random.Random(7000 + seed)
        graph = make_unlabeled_graph(
            n=80,
            degree=3,
            checkpoints=8,
            seed=seed,
        )

        direct_s = [0] * graph.n
        direct_i = [0] * graph.n
        propagated_s = [0] * graph.n
        propagated_i = [0] * graph.n

        # Each tuple represents one active direct source label.
        active = set()

        for step in range(100):
            should_remove = bool(active) and rng.random() < 0.40

            if should_remove:
                kind, node, bit = rng.choice(sorted(active))
                active.remove((kind, node, bit))

                gbsr_apply_removal(
                    graph,
                    direct_s,
                    direct_i,
                    propagated_s,
                    propagated_i,
                    kind,
                    node,
                    bit,
                )
            else:
                kind = rng.choice(["sensitive", "untrusted"])
                node = rng.randrange(graph.n)
                bit = 1 << rng.randrange(3)
                event = (kind, node, bit)

                if event in active:
                    continue

                active.add(event)

                if kind == "sensitive":
                    direct_s[node] |= bit
                else:
                    direct_i[node] |= bit

                gbsr_apply_update(
                    graph,
                    propagated_s,
                    propagated_i,
                    event,
                )

            expected_s, expected_i = reference_propagation(
                graph,
                direct_s,
                direct_i,
            )

            assert propagated_s == expected_s, (
                f"Sensitive mismatch at seed={seed}, step={step}"
            )
            assert propagated_i == expected_i, (
                f"Untrusted mismatch at seed={seed}, step={step}"
            )


def test_removing_one_of_two_same_bit_sources_preserves_other_source():
    graph = make_unlabeled_graph(
        n=80,
        degree=3,
        checkpoints=8,
        seed=123,
    )

    # Choose two source nodes and deliberately give them the same bit.
    node_a, node_b = 3, 7
    bit = 1

    direct_s = [0] * graph.n
    direct_i = [0] * graph.n
    propagated_s = [0] * graph.n
    propagated_i = [0] * graph.n

    # Make node_a and node_b sources of the same sensitive label.
    for node in (node_a, node_b):
        event = ("sensitive", node, bit)
        direct_s[node] |= bit
        gbsr_apply_update(
            graph, propagated_s, propagated_i, event
        )

    expected_s, expected_i = reference_propagation(
        graph, direct_s, direct_i
    )
    assert propagated_s == expected_s
    assert propagated_i == expected_i

    gbsr_apply_removal(
        graph,
        direct_s,
        direct_i,
        propagated_s,
        propagated_i,
        "sensitive",
        node_a,
        bit,
    )

    expected_s, expected_i = reference_propagation(
        graph, direct_s, direct_i
    )
    assert propagated_s == expected_s
    assert propagated_i == expected_i

    # The remaining source still carries the bit.
    assert direct_s[node_b] & bit
    assert propagated_s[node_b] & bit
