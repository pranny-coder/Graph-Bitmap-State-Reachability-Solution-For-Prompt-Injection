import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phase2a_dynamic_updates import make_unlabeled_graph, make_events, run_sequence  # noqa: E402


def test_dynamic_incremental_updates_match_full_recomputation_after_every_update():
    for seed in (7, 17, 31):
        graph = make_unlabeled_graph(100, 4, 20, seed)
        events = make_events(graph, 8, seed)
        traversal, _ = run_sequence(graph, events, "graph_traversal")
        bitmap, _ = run_sequence(graph, events, "bitmap_recompute")
        gbsr, _ = run_sequence(graph, events, "gbsr_incremental")
        assert traversal == bitmap == gbsr


def test_dynamic_update_on_source_with_no_descendants_is_correct():
    graph = make_unlabeled_graph(25, 0, 25, 3)
    events = make_events(graph, 8, 3)
    outputs = [run_sequence(graph, events, m)[0] for m in
               ("graph_traversal", "bitmap_recompute", "gbsr_incremental")]
    assert outputs[0] == outputs[1] == outputs[2]


def test_sensitive_and_untrusted_labels_remain_separate():
    graph = make_unlabeled_graph(40, 3, 40, 8)
    events = make_events(graph, 8, 8)
    outputs = [run_sequence(graph, events, m)[0] for m in
               ("graph_traversal", "bitmap_recompute", "gbsr_incremental")]
    assert outputs[0] == outputs[1] == outputs[2]
    assert any(s_mask for snapshot in outputs[0] for s_mask, _ in snapshot)
    assert any(i_mask for snapshot in outputs[0] for _, i_mask in snapshot)
