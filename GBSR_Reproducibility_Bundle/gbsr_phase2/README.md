# GBSR Phase 2 — Controlled security experiment

This phase evaluates **policy enforcement and selective component withholding** in a deterministic mock-agent harness. It is designed to test the graph/bitmap mechanics before integrating a real LLM.

## Critical limitation

The synthetic scenarios carry **oracle provenance**: the harness knows which protected or untrusted source contributes to each node because the scenario author explicitly encodes dependency edges. Therefore, passing this phase does **not** prove that GBSR can automatically detect semantic influence, paraphrased secrets, hidden prompt injection, or real-world leakage in an LLM. The paraphrased-secret case is blocked because its graph is explicitly labeled as derived from the secret. Phase 3 must test whether real provenance instrumentation can establish such labels.

The prototype uses synthetic strings only. It does not connect to real email, accounts, APIs, or private data.

## What is compared?

Four methods receive the same scenarios and policy:

1. `none`: no enforcement; executes every proposed action and releases every segment.
2. `graph_traversal`: recomputes provenance by reverse graph traversal at each output/action checkpoint.
3. `bitmap`: performs a topological provenance propagation pass, then checks bitmasks at each checkpoint.
4. `gbsr`: uses worklist/delta bitmap propagation, traces only conflict-relevant ancestors for diagnostics, withholds a conflicting output component, and performs a final assembled-output check.

For the first experiment, all defended methods intentionally share the same restrictive policy so their security outcomes can be compared fairly. The graph-only and bitmap methods are reference implementations, not claims that the methods are themselves novel.

## Requirements

- Python 3.10 or newer
- Main experiment uses only the Python standard library
- `pytest` is needed only for tests

## Run Phase 2

From the directory where this README is located:

```bash
python3 phase2_gbsr.py --trials 10 --out results
```

This writes:

- `results/phase2_summary.csv` — one row per scenario/method with leakage, action authorization, utility-retention and timing metrics.
- `results/phase2_traces.jsonl` — decision-level trace rows for debugging.

Use more timing repetitions after checking correctness:

```bash
python3 phase2_gbsr.py --trials 100 --out results_100
```

Run unit tests:

```bash
python3 -m pip install pytest
python3 -m pytest -q
```

## Expected behavior

- `none` leaks the synthetic secrets and executes the attacker-selected email recipient in the corresponding fixtures.
- `graph_traversal`, `bitmap`, and `gbsr` block sensitive output components.
- The mixed-content scenario preserves two public components and withholds one protected component.
- GBSR blocks an email redirected to `attacker@evil.test` and permits the same public email to `alice@corp.test`.
- All three defended implementations should produce the same release/action decisions on these fixtures.

If any of these checks fail, fix the logic before expanding the experiment.

## Metrics and their interpretation

- `restricted_segments_released`: number of output segments carrying a forbidden source label that were released.
- `unauthorized_actions_executed`: count of executed tool actions violating the user-authorized recipient or the sensitive-data policy.
- `safe_segment_retention_rate`: retained safe segments divided by safe segments available.
- `nodes_examined`, `edges_examined`, `trace_nodes_examined`: algorithmic work counters for rough comparison. Implementations count different operations, so interpret them as diagnostic counters, not interchangeable units of hardware work.
- `median_runtime_ms`: median measured run time across the selected trials. Small fixture timings are noisy; do not make strong speed claims from them.

## Fairness notes

- Graph construction is excluded from measured execution time; every method uses the same prebuilt graph.
- The `graph_traversal` reference visits ancestors to calculate labels at checkpoints; bitmap approaches compute labels before checking.
- The scenarios are deterministic and small. They do not constitute statistical estimates of real-world attack success.
- No actual LLM calls are made (`model_calls=0`).
- The GBSR output repair is intentionally **component-level withholding**. It does not generate semantic redactions or guarantee that a paraphrased secret will be recognized if its provenance label was lost.
- The final verifier checks known bitmap labels. It cannot independently detect an unlabeled semantic leak.

## Recommended next step after Phase 2 passes

Integrate one real, local or hosted LLM into a benchmark such as [AgentDojo](https://github.com/ethz-spylab/agentdojo). Instrument retrievals, intermediate values, memory and tool calls. Keep the model, prompts, attacks, and policies identical across baselines. Compare against established defenses and count both actual disclosures and legitimate task completion. Do not claim prompt-injection robustness from the synthetic Phase 2 results alone.
