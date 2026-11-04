import random

from phase3_agentdojo_gbsr import _new_state, _add_graph_node


def test_randomized_dags_match_ancestor_reference():
    # Multiple deterministic seeds make failures reproducible.
    for seed in range(25):
        rng = random.Random(seed)
        state = _new_state()
        node_count = 80

        # Generate an append-only DAG: every edge points to an earlier node.
        for index in range(node_count):
            possible_parents = list(range(index))
            parents = [
                parent
                for parent in possible_parents
                if rng.random() < 0.07
            ]

            direct_sensitive = rng.getrandbits(5)
            direct_influence = rng.getrandbits(5)

            node_id = _add_graph_node(
                state,
                "test_node",
                f"node_{index}",
                direct_sensitive,
                direct_influence,
                parents=parents,
            )

            assert node_id == index

        nodes = state["nodes"]
        nodes_by_id = {node["id"]: node for node in nodes}

        # Independent reference: walk every transitive ancestor and OR
        # their direct source labels, rather than using stored propagated masks.
        for node in nodes:
            expected_sensitive = node["direct_sensitive_mask"]
            expected_influence = node["direct_influence_mask"]

            stack = list(node["parents"])
            visited = set()

            while stack:
                ancestor_id = stack.pop()
                if ancestor_id in visited:
                    continue

                visited.add(ancestor_id)
                ancestor = nodes_by_id[ancestor_id]

                expected_sensitive |= ancestor["direct_sensitive_mask"]
                expected_influence |= ancestor["direct_influence_mask"]

                stack.extend(ancestor["parents"])

            assert node["sensitive_mask"] == expected_sensitive, (
                f"Sensitive mismatch: seed={seed}, node={node['id']}"
            )
            assert node["influence_mask"] == expected_influence, (
                f"Influence mismatch: seed={seed}, node={node['id']}"
            )

        # Verify the conservative session-wide summaries independently.
        expected_session_sensitive = 0
        expected_session_influence = 0

        for node in nodes:
            expected_session_sensitive |= node["sensitive_mask"]
            expected_session_influence |= node["influence_mask"]

        assert state["sensitive_mask"] == expected_session_sensitive
        assert state["influence_mask"] == expected_session_influence
