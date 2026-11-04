# GBSR reproducibility bundle

This bundle contains the revised experiment report, source scripts, Phase 2 CSV/JSONL results and an earlier 3-repeat Phase 3 Ollama pilot console log.

## Important data availability note

The newer user-local file `results/verification_phase3_ollama/phase3_ollama_summary.csv` and its paired trace file were not available in the shared artifact set used to build this bundle. The report distinguishes observations relayed by the user from values recalculated from bundled artifacts; it does not invent the missing CSV rows. Add that CSV and its paired JSONL trace here before computing aggregate statistics for the latest verification run.

## Main results

- Static benchmark: GBSR from-scratch worklist matched reference output but was slower than bitmap in 57/60 tested configurations.
- Dynamic benchmark: incremental GBSR matched reference states and showed lower runtime than repeated recomputation in the tested synthetic update workload.
- Support-count follow-up: per-conversation-reported seed experiments are summarized separately because the raw CSVs are absent from this bundle.
- Phase 3: the context-taint gate is deterministic and conservative. `gbsr_blocked=1` means output was withheld because restricted context was present; it does not mean the raw model output leaked the canary.
