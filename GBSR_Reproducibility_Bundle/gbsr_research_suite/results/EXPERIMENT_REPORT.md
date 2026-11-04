# GBSR Experiment Report — Pilot Results

This report is generated from the included result CSVs and tests. Timings are from the current execution environment, not expected timings for the user’s Mac. Results are a pilot, not publication-grade estimates.

## Correct treatment of the oracle-label warning

Do not suppress the oracle-label warning in Phase 2A or Phase 2B. The synthetic graph source labels are known by construction; these phases test propagation and enforcement correctness, not whether an LLM can infer semantic provenance. Phase 3 uses different, imperfect sources of labels: conservative context-exposure labels in the Ollama pilot and tool-name heuristics/context union in the AgentDojo adapter.

## Phase 2A static graph benchmark

Configuration: 1,000 / 5,000 / 10,000 nodes; requested average out-degree 2 / 8; 10 / 100 checkpoints; seeds 7 / 17 / 27 / 37 / 47; 3 timing repeats; 180 method rows. All rows returned labels matching the graph-traversal reference.

| Method | Rows | Median of configuration-level median runtimes (ms) | Min (ms) | Max (ms) |
|---|---:|---:|---:|---:|
| bitmap | 60 | 2.4742 | 0.1693 | 12.5221 |
| gbsr | 60 | 5.3510 | 0.3078 | 52.1356 |
| graph_traversal | 60 | 2.3809 | 0.0146 | 181.9961 |

**Interpretation:** the current from-scratch GBSR worklist was slower than the full topological bitmap pass in 57/60 configurations; median GBSR/bitmap ratio 3.27× (range 0.92–5.95). The simple bitmap pass is very strong when all labels are present up front. Graph traversal can be competitive when there are few checkpoints and their ancestor sets are small.

## Phase 2A dynamic source-update benchmark

Configuration: 1,000 / 5,000 nodes; average out-degree 2 / 8; 10 / 100 checkpoints; 8 new labels arriving one at a time (4 sensitive-source bits and 4 untrusted-source bits); seeds 7 / 17; 2 timing repeats; 48 method rows. All methods matched the graph-traversal reference after every update.

| Method | Rows | Median of sequence runtimes (ms) | Min (ms) | Max (ms) |
|---|---:|---:|---:|---:|
| bitmap_recompute | 16 | 7.5983 | 1.4202 | 27.0908 |
| gbsr_incremental | 16 | 0.4959 | 0.0781 | 7.0043 |
| graph_traversal | 16 | 13.0520 | 0.1221 | 437.4366 |

**Interpretation:** under this deliberately incremental workload, GBSR updated persisted masks and was faster than full bitmap recomputation in 16/16 configurations, with median speedup 10.27× (range 3.83–35.04). It was faster than re-walking every checkpoint’s ancestor set in 16/16 configurations, with median speedup 30.91×. This supports the incremental-update hypothesis only for the tested synthetic workload. It does not establish a general performance win or novelty. The dynamic test has just two seeds and eight source events; more seeds, graph families, sources, and update patterns are needed.

## Phase 2B adversarial correctness tests

Dedicated adversarial tests: 5 passed. Full suite: 15 passed. Coverage includes combined sensitive sources, recipient-specific policy masks, split-secret segments, method parity, dynamic update parity, a known failure when semantic content has no provenance label, and overblocking from a conservative Phase 3 gate.

The unlabeled-composition test intentionally demonstrates a limitation: if the provenance system fails to label semantically revealing fragments, bitmaps cannot recover that missing causal information by themselves.

## Phase 3 status

The Phase 3 code is prepared, but no real-model benchmark was executed in this container because it has no Ollama endpoint and AgentDojo is not installed. `phase3_ollama_pilot.py` is ready to run against a local Ollama model, and `phase3_agentdojo_gbsr.py` is a best-effort adapter for AgentDojo’s custom pipeline interface. Validate its imports and task IDs against the installed AgentDojo version before using results. The official docs say the package API is still under development and document `pip install agentdojo`, custom pipeline elements, and benchmark functions: https://agentdojo.spylab.ai/ and https://agentdojo.spylab.ai/concepts/agent_pipeline/.

The local Ollama pilot uses context-exposure taint: if a private record was visible to the model, the whole completion is withheld. A new benign task with a secret in context measures this method’s false-positive/utility cost. The AgentDojo adapter uses tool-name heuristics plus conservative context union—not exact semantic provenance. Report security and utility together.

## Research takeaway

The static results caution against claiming the current GBSR implementation is generally faster. The dynamic results give a plausible reason to continue: persistent delta propagation can avoid repeated full propagation when new source labels arrive over time. The next research step is to expand dynamic tests, add optimized incremental baselines, and test actual LLM outputs and tool effects. Neither synthetic experiment proves prompt-injection resistance or algorithmic novelty.
