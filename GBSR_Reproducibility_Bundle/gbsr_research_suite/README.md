# GBSR research suite — Phase 2A, Phase 2B, Phase 3

This suite contains three distinct experiments. Keep their claims separate.

## The warning: what it means and how to handle it

**Do not delete or hide the oracle-label warning in Phase 2.** Phase 2's synthetic source labels are known by construction. That is appropriate for testing whether graph traversal, topological bitmap propagation, and incremental bitmap propagation compute the same labels and enforce a stated policy.

Phase 2A tests data-structure cost/correctness. Phase 2B tests edge cases of policy mechanics. Neither can establish automatic semantic provenance in an LLM.

Phase 3 adds a real LLM in one of two ways:
1. `phase3_ollama_pilot.py`: tests an Ollama model; it uses conservative **context-exposure taint**, not oracle causal labels. If a sensitive record was present anywhere in the prompt, the whole completion inherits that source's bit. This can block benign answers, but avoids pretending to know which sentence the model used.
2. `phase3_agentdojo_gbsr.py`: adds GBSR instrumentation to AgentDojo's pipeline. Source labels are heuristically assigned from configurable tool-name substrings and propagated conservatively across context. This is real-model benchmark instrumentation, but it is still not exact semantic attribution. The heuristic must be adapted to the benchmark suite's actual tool semantics.

There is no general-purpose automatic method here that can prove which data semantically caused a paraphrase. Keep that limitation visible in the paper and results.

## Setup

Python 3.10+ is recommended. From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pytest -q
```

Core Phase 2 experiments use only the Python standard library; pytest is used for tests.

## Phase 2A — scalability benchmark

Quick pilot:

```bash
python3 phase2a_scalability.py \
  --sizes 1000 5000 10000 \
  --avg-out-degrees 2 8 \
  --checkpoints 10 100 \
  --seeds 7 17 \
  --repeats 2 \
  --out results/phase2a_scalability.csv
```

For a larger run (slower):

```bash
python3 phase2a_scalability.py \
  --sizes 1000 5000 10000 25000 \
  --avg-out-degrees 2 8 16 \
  --checkpoints 10 100 500 \
  --seeds 7 17 27 37 47 \
  --repeats 5 \
  --out results/phase2a_scalability_large.csv
```

Methods:
- `graph_traversal`: recalculates source masks by walking ancestors separately for each output checkpoint.
- `bitmap`: propagates masks once in topological order, then queries checkpoints.
- `gbsr`: uses worklist/delta bitmap propagation and checks each checkpoint's own bitmap. Because output components already carry their own labels, the graph need not be traversed merely to decide whether that component is restricted.

To measure the cost of the original per-conflict diagnostic trace, on a small graph only, add `--include-trace-ablation`. It adds a fourth row group called `gbsr_with_trace`. The first smoke test found this tracing step costly, so it is now an optional diagnostic: trace only when a composite output needs root-cause localization or repair guidance, not when the output component's bitmap already identifies the conflict.

CSV fields include graph size/edge count, requested and actual mean degree, checkpoint count, random seed, median/min/max runtime, peak Python allocation excluding the graph itself, nodes/edges examined, propagation updates, conflict traces, and correctness. Rows are flushed as each method completes, so a stopped long run retains earlier measurements. The default run is intended as a manageable multi-seed pilot; use the larger command only after this completes successfully.

**Fairness:** graph construction is excluded for all methods. Memory is measured in a separate `tracemalloc` pass because that instrumentation distorts timing. Node/edge counters describe each algorithm's work and are not directly interchangeable CPU instructions. Check output correctness before comparing speed. The labels remain known by construction here.

### Phase 2A-2 — dynamic source updates (especially relevant to GBSR)

The static benchmark above assigns all source labels before propagation. That setting favors a simple full topological bitmap pass, and it is a necessary baseline. Because the GBSR idea is incremental, also test when labels arrive over time and each new label forces the system to refresh output checkpoints.

Quick run:

```bash
python3 phase2a_dynamic_updates.py \
  --sizes 1000 5000 \
  --avg-out-degrees 2 8 \
  --checkpoints 10 100 \
  --source-events 8 \
  --seeds 7 17 \
  --repeats 2 \
  --out results/phase2a_dynamic_updates.csv
```

This compares ancestor traversal at each update, full topological bitmap recomputation after each update, and persistent GBSR delta propagation. Every method must return the same sensitive and untrusted masks after every source update before its runtime is considered. To broaden the study, add `--sizes 10000`, more seeds, and `--source-events 16`; dense graphs with many checkpoints can take substantially longer.

This is still a synthetic, known-label benchmark. It tests the cost of incremental label propagation, not automatic recognition of semantic influence in an LLM.

## Phase 2B — adversarial correctness tests

Run:

```bash
python3 -m pytest -q tests/test_phase2b_adversarial.py
```

Coverage includes multiple sensitive sources flowing into one output, recipient-specific disclosure masks, two sentences that each reveal part of the same tagged secret, parity across graph/bitmap/GBSR, and a known limitation where semantically revealing fragments have lost all labels.

The `test_known_limitation_unlabeled_semantic_composition_can_pass` test intentionally demonstrates a weakness: if an upstream labeler fails to mark a derived disclosure, the bitmap cannot recover that fact from nothing. Passing this test documents the limitation; it is not a security success.

The original synthetic Phase 2 command still works:

```bash
python3 phase2_gbsr.py --trials 25 --out results/phase2
```

For scenarios with no safe segments, the retention-rate field is now blank (`N/A`) rather than incorrectly reported as 1.0.

## Phase 3A — local real-LLM pilot with Ollama

Start Ollama and ensure your model is available:

```bash
ollama serve
# In another terminal, if needed:
ollama pull llama3.2
```

Run:

```bash
python3 phase3_ollama_pilot.py --model llama3.2 --repeats 3 --out results/phase3_ollama
```

Outputs:
- `phase3_ollama_summary.csv`
- `phase3_ollama_traces.jsonl`

This tests exact canary-token disclosure in four injected prompt variants, a public-only control, and a benign task where a private record is present in context but is not needed. GBSR uses conservative context-exposure labels: because the test private record is placed in the model context, the output is withheld. The extra benign-with-sensitive-context case deliberately measures overblocking and the privacy-vs-utility tradeoff. It does not prove the model actually followed the attack, and exact canary matching does not catch every paraphrase. For paraphrase leakage, add blinded human annotation and/or a validated semantic leakage evaluator; do not use an LLM judge as the sole security gate.

## Phase 3B — AgentDojo benchmark

AgentDojo is an established benchmark for tool-using agents with prompt-injection tasks. See its official repository and docs:
- https://github.com/ethz-spylab/agentdojo
- https://agentdojo.spylab.ai/

Install in the active environment:

```bash
python3 -m pip install agentdojo
```

For local Ollama's OpenAI-compatible endpoint:

```bash
export OPENAI_COMPATIBLE_BASE_URL="http://localhost:11434/v1"
export OPENAI_COMPATIBLE_API_KEY="ollama"
```

Run a small GBSR pilot with one task/injection first:

```bash
python3 phase3_agentdojo_gbsr.py \
  --suite workspace \
  --benchmark-version v1.2.2 \
  --model openai-compatible --model-id llama3.2 \
  --attack tool_knowledge \
  --user-tasks user_task_0 \
  --injection-tasks injection_task_0 \
  --logdir runs_gbsr --force-rerun
```

If your installed AgentDojo version uses different suite/task IDs or APIs, run the official CLI help and inspect `get_suite(...).user_tasks` / `injection_tasks`. AgentDojo's API has changed over time.

The adapter tracks tool outputs using configurable **tool-name substring heuristics**. For a credible evaluation, inspect the suite's tool names and edit the defaults in `phase3_agentdojo_gbsr.py` to reflect actual sources/actions. By default, it blocks risky tool calls after any untrusted or sensitive tool output and blocks final output after any sensitive tool output; this is intentionally conservative and can sharply reduce utility. Report utility and security together. Do not call the heuristic labels ground-truth causal attribution.

Run an undefended baseline with the official CLI for the same suite/model/tasks/attack, saving to a different log directory. Since the custom adapter enforces a more conservative policy than the baseline, include a relevant built-in defense such as `tool_filter` or `transformers_pi_detector` where the model/provider supports it. Compare the same task and injection subsets. The built-in tool filter is only supported for OpenAI models in AgentDojo's current documented implementation.

## Interpret results responsibly

- No defense should be compared under identical tasks, attacks, model, prompt, and access policy.
- Track actual tool effects and actual released content, not only whether the model said it would do something.
- Report attack success, task utility, safe-output retention, runtime, and memory.
- Phase 2A uses known labels by design; Phase 2B uses explicit ground truth; Phase 3 uses conservative/heuristic instrumented labels. These are different evidence levels.
- A performance win does not imply algorithmic novelty. Novelty requires a direct comparison with existing graph/provenance/IFC systems and a distinct algorithmic contribution.
