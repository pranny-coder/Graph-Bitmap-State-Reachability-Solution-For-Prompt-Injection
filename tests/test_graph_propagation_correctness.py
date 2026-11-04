from phase3_agentdojo_gbsr import _new_state, _add_graph_node


def get_node(state, node_id):
    return next(n for n in state["nodes"] if n["id"] == node_id)


def test_disconnected_node_does_not_inherit_sensitive_label():
    state = _new_state()

    secret = _add_graph_node(
        state, "source", "private_record", 1, 0, parents=[]
    )
    public = _add_graph_node(
        state, "source", "unrelated_public_record", 0, 0, parents=[]
    )

    assert get_node(state, secret)["sensitive_mask"] == 1
    assert get_node(state, public)["sensitive_mask"] == 0

    # The session-wide summary intentionally remains conservative.
    assert state["sensitive_mask"] == 1


def test_child_inherits_labels_from_its_parent():
    state = _new_state()

    secret = _add_graph_node(
        state, "source", "private_record", 1, 0, parents=[]
    )
    child = _add_graph_node(
        state, "derived", "summary", 0, 0, parents=[secret]
    )

    assert get_node(state, child)["sensitive_mask"] == 1
    assert get_node(state, child)["influence_mask"] == 0


def test_node_joins_labels_from_multiple_parents():
    state = _new_state()

    secret = _add_graph_node(
        state, "source", "private_record", 1, 0, parents=[]
    )
    untrusted = _add_graph_node(
        state, "source", "external_instruction", 0, 2, parents=[]
    )
    combined = _add_graph_node(
        state, "derived", "combined_result", 0, 0,
        parents=[secret, untrusted],
    )
    descendant = _add_graph_node(
        state, "derived", "final_result", 0, 0,
        parents=[combined],
    )

    assert get_node(state, combined)["sensitive_mask"] == 1
    assert get_node(state, combined)["influence_mask"] == 2
    assert get_node(state, descendant)["sensitive_mask"] == 1
    assert get_node(state, descendant)["influence_mask"] == 2


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
