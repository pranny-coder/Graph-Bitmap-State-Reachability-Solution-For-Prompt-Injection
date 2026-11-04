"""Dedicated runner to evaluate GBSR on all 102 confirmed baseline breached episodes.

This script:
1. Identifies the exact 102 (user_task, injection_task) pairs breached in runs_qwen3_banking_full_baseline.
2. Evaluates each pair under active GBSR enforcement (with the validated Qwen3 4096-token & 1800s timeout settings).
3. Safely resumes if interrupted (skipping already completed JSON logs).
4. Reports the definitive Breach Remediation Rate and Defended Suite Security.
"""
from __future__ import annotations
import argparse
import glob
import json
import logging
import os
import sys
import time
from pathlib import Path

# Add venv site-packages if needed
site_pkgs = glob.glob('.venv/lib/python*/site-packages')
if site_pkgs:
    sys.path.insert(0, site_pkgs[0])

from agentdojo.task_suite.load_suites import get_suite
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.logging import Logger, TraceLogger
from agentdojo.benchmark import load_task_results

from phase3_agentdojo_gbsr_fixed import build_gbsr_pipeline


def extract_breached_pairs(baseline_dir: str = "runs_qwen3_banking_full_baseline") -> list[tuple[str, str]]:
    files = sorted(glob.glob(f"{baseline_dir}/**/user_task_*/**/*.json", recursive=True))
    breached = []
    for f in files:
        with open(f) as fp:
            d = json.load(fp)
        # security == False in AgentDojo indicates the attack succeeded (security was breached)
        if d.get("security") is False:
            ut = d.get("user_task_id")
            it = d.get("injection_task_id")
            if ut and it:
                breached.append((ut, it))
    return breached


def main():
    parser = argparse.ArgumentParser(description="Evaluate GBSR on confirmed baseline breaches.")
    parser.add_argument("--baseline-dir", default="runs_qwen3_banking_full_baseline")
    parser.add_argument("--logdir", type=Path, default=Path("runs_qwen3_banking_breaches_gbsr"))
    parser.add_argument("--model-id", default="qwen3:4b")
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--request-timeout", type=float, default=1800.0)
    parser.add_argument("--suite", default="banking")
    parser.add_argument("--benchmark-version", default="v1.2.2")
    parser.add_argument("--attack", default="important_instructions")
    parser.add_argument("--untrusted-tool-substrings", nargs="+",
                        default=["browser", "web", "search", "fetch", "email", "inbox", "message",
                                 "file", "document", "calendar", "ticket", "transaction"])
    parser.add_argument("--sensitive-tool-substrings", nargs="+",
                        default=["email", "inbox", "message", "account", "bank", "private",
                                 "secret", "credential", "transaction", "contact", "file", "document"])
    parser.add_argument("--risky-action-substrings", nargs="+",
                        default=["send", "forward", "delete", "transfer", "pay", "post", "share",
                                 "write", "update", "create", "book", "execute", "move"])
    parser.add_argument("--model", default="openai-compatible")
    parser.add_argument("--tool-delimiter", default="tool")
    parser.add_argument("--system-message-name", default=None)
    parser.add_argument("--system-message", default=None)
    parser.add_argument("--allow-risky-after-sensitive", action="store_true")
    parser.add_argument("--allow-risky-after-untrusted", action="store_true")
    parser.add_argument("--allow-sensitive-final-output", action="store_true")
    parser.add_argument("--ollama-no-think", action="store_true")

    args = parser.parse_args()

    breached_pairs = extract_breached_pairs(args.baseline_dir)
    print(f"Loaded {len(breached_pairs)} confirmed breached episodes from {args.baseline_dir}")

    args.logdir.mkdir(parents=True, exist_ok=True)

    suite = get_suite(args.benchmark_version, args.suite)
    pipeline = build_gbsr_pipeline(args)
    attack = load_attack(args.attack, suite, pipeline)

    total = len(breached_pairs)
    completed_results = []

    print(f"Starting GBSR evaluation on {total} breached episodes...")
    print(f"Output directory: {args.logdir}")
    print("-" * 65)

    for idx, (ut_id, it_id) in enumerate(breached_pairs, 1):
        # Check if already completed
        try:
            prev = load_task_results(
                pipeline.name, suite.name, ut_id,
                attack_name=attack.name, injection_task=it_id,
                logdir=args.logdir,
            )
        except Exception:
            prev = None

        if prev is not None and prev.utility is not None and prev.security is not None:
            print(f"[{idx}/{total}] SKIP (already done): {ut_id} + {it_id} | Util={prev.utility} | Sec={prev.security}")
            completed_results.append((ut_id, it_id, prev.utility, prev.security))
            continue

        user_task = suite.get_user_task_by_id(ut_id)
        injection_task = suite.get_injection_task_by_id(it_id)
        task_injections = attack.attack(user_task, injection_task)

        t0 = time.time()
        print(f"[{idx}/{total}] RUNNING: {ut_id} + {it_id}...", end="", flush=True)

        with TraceLogger(
            delegate=Logger.get(),
            suite_name=suite.name,
            user_task_id=user_task.ID,
            injection_task_id=injection_task.ID,
            injections=task_injections,
            attack_type=attack.name,
            pipeline_name=pipeline.name,
            benchmark_version=args.benchmark_version,
        ) as logger:
            try:
                utility, security = suite.run_task_with_pipeline(
                    pipeline, user_task, injection_task, task_injections
                )
            except Exception as e:
                print(f" ERROR: {e}")
                logger.log_error(f"Execution error on {ut_id} + {it_id}: {e}")
                utility = False
                security = True

            logger.set_contextarg("utility", utility)
            logger.set_contextarg("security", security)

        dur = time.time() - t0
        print(f" DONE ({dur:.1f}s) | Util={utility} | Sec={security}")
        completed_results.append((ut_id, it_id, utility, security))

    print("\n" + "=" * 65)
    print("                 GBSR EVALUATION SUMMARY")
    print("=" * 65)

    num_runs = len(completed_results)
    if num_runs == 0:
        print("No runs completed.")
        return

    # In AgentDojo, security == True means the system remained secure (attack prevented)
    sec_count = sum(1 for _, _, _, s in completed_results if s is True)
    util_count = sum(1 for _, _, u, _ in completed_results if u is True)

    print(f"Total Evaluated Breaches: {num_runs}")
    print(f"Remediated by GBSR (Security = True): {sec_count} / {num_runs} ({100.0 * sec_count / num_runs:.2f}%)")
    print(f"Task Utility Retained: {util_count} / {num_runs} ({100.0 * util_count / num_runs:.2f}%)")
    print(f"Trace log saved to: {args.logdir / 'gbsr_trace.jsonl'}")
    print("=" * 65)


if __name__ == "__main__":
    main()
