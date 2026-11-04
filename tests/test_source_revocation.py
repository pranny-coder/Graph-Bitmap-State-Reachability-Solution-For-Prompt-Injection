from types import SimpleNamespace

import pytest

from phase2a_dynamic_updates import gbsr_apply_removal


def full_propagation(graph, direct_s, direct_i):
    """Independent reference propagation over a topologically ordered DAG."""
    propagated_s = list(direct_s)
    propagated_i = list(direct_i)

    for parent in range(graph.n):
        for child in graph.children[parent]:
            propagated_s[child] |= propagated_s[parent]
            propagated_i[child] |= propagated_i[parent]

    return propagated_s, propagated_i


def make_shared_path_graph():
    # 0 -> 2 -> 3
    # 1 -> 2
    # 0 -> 4 -> 5
    #
    # Sources 0 and 1 share bit 1.
    parents = [
        [],
        [],
        [0, 1],
        [2],
        [0],
        [4],
    ]
    children = [[] for _ in parents]

    for child, parent_list in enumerate(parents):
        for parent in parent_list:
            children[parent].append(child)

    return SimpleNamespace(
        n=len(parents),
        parents=parents,
        children=children,
    )


@pytest.mark.parametrize("kind", ["sensitive", "untrusted"])
def test_removal_preserves_shared_labels_and_clears_exclusive_branch(kind):
    graph = make_shared_path_graph()
    bit = 1

    direct_s = [bit, bit, 0, 0, 0, 0] if kind == "sensitive" else [0] * 6
    direct_i = [bit, bit, 0, 0, 0, 0] if kind == "untrusted" else [0] * 6

    propagated_s, propagated_i = full_propagation(
        graph, direct_s, direct_i
    )

    # Before removal, both branches inherit the label from source 0.
    # The shared branch also has independent support from source 1.
    assert (propagated_s if kind == "sensitive" else propagated_i)[3] == bit
    assert (propagated_s if kind == "sensitive" else propagated_i)[5] == bit

    nodes_examined, edges_examined = gbsr_apply_removal(
        graph,
        direct_s,
        direct_i,
        propagated_s,
        propagated_i,
        kind,
        source_node=0,
        bit=bit,
    )

    expected_s, expected_i = full_propagation(
        graph, direct_s, direct_i
    )

    assert propagated_s == expected_s
    assert propagated_i == expected_i

    labels = propagated_s if kind == "sensitive" else propagated_i

    assert labels[0] == 0       # Removed source no longer carries the bit.
    assert labels[2] == bit     # Source 1 still reaches the shared node.
    assert labels[3] == bit     # The shared descendant retains the bit.
    assert labels[4] == 0       # Exclusive branch loses the bit.
    assert labels[5] == 0

    assert nodes_examined > 0
    assert edges_examined >= 0
