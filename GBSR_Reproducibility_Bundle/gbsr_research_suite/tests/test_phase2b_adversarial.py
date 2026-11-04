"""Phase 2B adversarial correctness tests for the synthetic, oracle-labeled harness.

These tests check policy mechanics, not an LLM's ability to infer hidden semantics.
"""
from dataclasses import replace
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phase2_gbsr import (  # noqa: E402
    BANK, PRIVATE_EMAIL, CREDENTIAL, ProvenanceGraph, Scenario, Segment,
    _run_once, build_scenarios,
)


def make_combined_secret_scenario():
    g = ProvenanceGraph()
    g.add_node("src_bank", "sensitive_source", sensitive_mask=BANK, payload="bank-fragment")
    g.add_node("src_mail", "sensitive_source", sensitive_mask=PRIVATE_EMAIL, payload="mail-fragment")
    g.add_node("merge", "transformation", payload="combined-secret")
    g.add_node("out", "output", payload="combined-secret")
    g.add_edge("src_bank", "merge")
    g.add_edge("src_mail", "merge")
    g.add_edge("merge", "out")
    return Scenario(
        name="combined_secret",
        description="Two sensitive sources are combined into one output.",
        graph=g,
        segments=[Segment("combined", "out", "Combined sensitive value.")],
        forbidden_sensitive_mask=BANK | PRIVATE_EMAIL,
    )


def make_split_secret_scenario():
    g = ProvenanceGraph()
    g.add_node("credential", "sensitive_source", sensitive_mask=CREDENTIAL, payload="ORCHID-7421")
    g.add_node("part_a", "output_segment", payload="The recovery code starts with ORCHID.")
    g.add_node("part_b", "output_segment", payload="Its numeric suffix is 7421.")
    g.add_edge("credential", "part_a")
    g.add_edge("credential", "part_b")
    return Scenario(
        name="split_secret",
        description="Each sentence reveals one part of the same protected secret.",
        graph=g,
        segments=[
            Segment("part_a", "part_a", "The recovery code starts with ORCHID."),
            Segment("part_b", "part_b", "Its numeric suffix is 7421."),
        ],
        forbidden_sensitive_mask=CREDENTIAL,
    )


def test_multiple_sensitive_sources_are_unioned_in_bitmap():
    scenario = make_combined_secret_scenario()
    labels, _, _, _ = scenario.graph.propagate_incrementally()
    assert labels["out"][0] == BANK | PRIVATE_EMAIL
    outcome = _run_once(scenario, "gbsr")
    assert outcome.segment_leaks == 0
    assert outcome.blocked_segment_ids == ["combined"]


def test_policy_is_destination_specific():
    base = build_scenarios()["direct_secret_disclosure"]
    external_policy = replace(base, forbidden_sensitive_mask=BANK)
    authorized_internal_policy = replace(base, forbidden_sensitive_mask=0)
    external_result = _run_once(external_policy, "gbsr")
    internal_result = _run_once(authorized_internal_policy, "gbsr")
    assert external_result.blocked_segment_ids == ["secret_answer"]
    assert internal_result.released_segment_ids == ["secret_answer"]
    assert external_result.segment_leaks == 0
    assert internal_result.segment_leaks == 0  # BANK is allowed under this test policy.


def test_both_split_sentences_are_blocked_if_each_carries_secret_provenance():
    scenario = make_split_secret_scenario()
    outcome = _run_once(scenario, "gbsr")
    assert outcome.released_segment_ids == []
    assert set(outcome.blocked_segment_ids) == {"part_a", "part_b"}
    assert outcome.segment_leaks == 0


def test_graph_and_bitmap_baselines_match_for_combined_and_split_secrets():
    for scenario in (make_combined_secret_scenario(), make_split_secret_scenario()):
        outcomes = [_run_once(scenario, method) for method in ("graph_traversal", "bitmap", "gbsr")]
        assert outcomes[0].released_segment_ids == outcomes[1].released_segment_ids == outcomes[2].released_segment_ids


def test_known_limitation_unlabeled_semantic_composition_can_pass():
    """Explicitly capture a known failure mode instead of silently claiming security.

    If both fragments that jointly reconstruct a secret receive zero source labels,
    the bitmap has no evidence of that semantic relationship and GBSR may release them.
    Phase 3 should measure this limitation; this test is expected to demonstrate it.
    """
    g = ProvenanceGraph()
    g.add_node("fragment_a", "output_segment", payload="The first word is ORCHID.")
    g.add_node("fragment_b", "output_segment", payload="The number is 7421.")
    scenario = Scenario(
        name="unlabeled_composition_limit",
        description="Two unlabeled fragments jointly reveal a secret.",
        graph=g,
        segments=[
            Segment("a", "fragment_a", "The first word is ORCHID."),
            Segment("b", "fragment_b", "The number is 7421."),
        ],
        forbidden_sensitive_mask=CREDENTIAL,
    )
    outcome = _run_once(scenario, "gbsr")
    # This assertion intentionally documents a limitation of missing attribution.
    assert outcome.released_segment_ids == ["a", "b"]
    assert outcome.segment_leaks == 0  # Graph labels say safe; semantic reality says otherwise.
