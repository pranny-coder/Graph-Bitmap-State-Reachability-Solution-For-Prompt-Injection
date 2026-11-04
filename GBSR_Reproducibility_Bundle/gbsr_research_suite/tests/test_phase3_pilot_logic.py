import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phase3_ollama_pilot import (  # noqa: E402
    SENSITIVE_SECRET, UNTRUSTED_WEB, FORBIDDEN_EXTERNAL_MASK,
    context_exposure_masks, gate_output, build_cases,
)


def test_context_exposure_union_sets_sensitive_and_untrusted_bits():
    case = next(c for c in build_cases() if c["case_id"] == "injection_secret_1")
    sensitive, untrusted = context_exposure_masks(case["items"])
    assert sensitive & SENSITIVE_SECRET
    assert untrusted & UNTRUSTED_WEB


def test_gate_allows_public_only_context():
    result = gate_output("Maintenance is Tuesday.", 0, FORBIDDEN_EXTERNAL_MASK)
    assert result["released"] is True
    assert result["output"] == "Maintenance is Tuesday."


def test_gate_blocks_whole_completion_when_secret_was_in_context():
    result = gate_output("The code is ORCHID-7421.", SENSITIVE_SECRET, FORBIDDEN_EXTERNAL_MASK)
    assert result["released"] is False
    assert "ORCHID-7421" not in result["output"]


def test_benign_task_with_sensitive_context_is_overblocked_by_conservative_gate():
    case = next(c for c in build_cases() if c["case_id"] == "benign_public_sensitive_context")
    assert case["should_be_blocked"] is False  # The requested task is benign.
    sensitive, _ = context_exposure_masks(case["items"])
    result = gate_output("Maintenance is Tuesday.", sensitive, FORBIDDEN_EXTERNAL_MASK)
    assert result["released"] is False  # Deliberate false-positive/utility tradeoff.
