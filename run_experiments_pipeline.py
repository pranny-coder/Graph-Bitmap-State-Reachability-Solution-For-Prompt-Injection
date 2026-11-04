"""
Benchmark runner for the experimental conditions:
1. Benign Clean Run (Guard OFF): 16 tasks without injection
2. Benign Clean Run (Guard ON - Session Taint): 16 tasks without injection
3. Always-Block Mutating Tools Baseline: Blanket block of all state-mutating calls
4. Argument-Gated Provenance Baseline: Fine-grained argument provenance verification
"""
import sys
import os
from pathlib import Path
import json

# Ensure .venv is in sys.path
_venv_site = Path(__file__).resolve().parent / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if _venv_site.is_dir() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))

import phase3_agentdojo_gbsr_fixed as p3
import argument_provenance_gate as apg
from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
from agentdojo.agent_pipeline.tool_execution import ToolsExecutor, ToolsExecutionLoop
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.benchmark import benchmark_suite_without_injections
from agentdojo.types import ChatToolResultMessage, text_content_block_from_string
from openai import OpenAI


class AlwaysBlockToolsExecutor(ToolsExecutor):
    """Blanket baseline: Always blocks any state-mutating tool call regardless of context."""
    def query(self, query, runtime, env=None, messages=(), extra_args=None):
        extra_args = {} if extra_args is None else extra_args
        if not messages or p3._field(messages[-1], "role") != "assistant":
            return query, runtime, env, messages, extra_args
        tool_calls = p3._field(messages[-1], "tool_calls")
        if not tool_calls:
            return query, runtime, env, messages, extra_args

        results = []
        for call in tool_calls:
            name = str(p3._field(call, "function", ""))
            call_id = p3._field(call, "id")
            args = dict(p3._field(call, "args", {}) or {})

            if p3.is_state_mutating_tool(name):
                reason = f"AlwaysBlock policy: blocked state-mutating tool call '{name}'."
                results.append(ChatToolResultMessage(
                    role="tool", content=[text_content_block_from_string(reason)],
                    tool_call_id=call_id, tool_call=call, error=reason,
                ))
                continue

            tool_result, error = runtime.run_function(env, name, args)
            formatted = self.output_formatter(tool_result)
            results.append(ChatToolResultMessage(
                role="tool", content=[text_content_block_from_string(formatted)],
                tool_call_id=call_id, tool_call=call, error=error,
            ))
        return query, runtime, env, [*messages, *results], extra_args


class ArgumentGatedToolsExecutor(ToolsExecutor):
    """
    Fine-grained Argument-Level Provenance Executor:
    1. If a mutating tool call is proposed, check if its arguments originate from trusted prompt/fields.
    2. If all arguments are trusted, the call is permitted even if earlier context was tainted.
    3. If any argument is tainted or an injection target, the call is intercepted.
    """
    def __init__(self):
        super().__init__()
        self.sensitive_substrings = ["email", "inbox", "message", "account", "bank", "private", "secret", "credential", "transaction", "contact", "file", "document"]
        self.untrusted_substrings = ["browser", "web", "search", "fetch", "email", "inbox", "message", "file", "document", "calendar", "ticket", "transaction"]

    def query(self, query, runtime, env=None, messages=(), extra_args=None):
        extra_args = {} if extra_args is None else extra_args
        if not messages or p3._field(messages[-1], "role") != "assistant":
            return query, runtime, env, messages, extra_args
        tool_calls = p3._field(messages[-1], "tool_calls")
        if not tool_calls:
            return query, runtime, env, messages, extra_args

        state = p3._get_state(extra_args)
        
        # Extract prompt tokens from the initial user query
        user_prompt = ""
        for m in messages:
            if p3._field(m, "role") == "user":
                user_prompt += " " + str(p3._field(m, "content", ""))
        prompt_tokens = apg.extract_prompt_tokens(user_prompt)

        results = []
        for call in tool_calls:
            name = str(p3._field(call, "function", ""))
            call_id = p3._field(call, "id")
            args = dict(p3._field(call, "args", {}) or {})

            # If tool is mutating, evaluate argument provenance
            if p3.is_state_mutating_tool(name):
                allowed, reason, needs_conf = apg.check_argument_provenance(
                    name, args, prompt_tokens, state
                )
                if not allowed:
                    block_msg = f"GBSR argument gate blocked '{name}': {reason}"
                    state["blocked_tool_calls"] = state.get("blocked_tool_calls", 0) + 1
                    results.append(ChatToolResultMessage(
                        role="tool", content=[text_content_block_from_string(block_msg)],
                        tool_call_id=call_id, tool_call=call, error=block_msg,
                    ))
                    continue

            # Execute tool safely
            tool_result, error = runtime.run_function(env, name, args)
            formatted = self.output_formatter(tool_result)

            # Update taint state if tool returns untrusted/sensitive content
            if p3._matches(name, self.untrusted_substrings):
                state["influence_mask"] = int(state.get("influence_mask", 0)) | 1
            if p3._matches(name, self.sensitive_substrings):
                state["sensitive_mask"] = int(state.get("sensitive_mask", 0)) | 1

            results.append(ChatToolResultMessage(
                role="tool", content=[text_content_block_from_string(formatted)],
                tool_call_id=call_id, tool_call=call, error=error,
            ))
        return query, runtime, env, [*messages, *results], extra_args


def build_pipeline(mode="guard_on", model_id="qwen3:4b", timeout=1800.0, max_tokens=4096, logdir=Path("runs_exp")):
    base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
    api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "ollama")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    custom_llm = p3.BoundedOpenAILLM(client, model_id, max_tokens, no_think=False)
    custom_llm.name = model_id

    config = PipelineConfig(
        llm=custom_llm,
        model_id=model_id,
        defense=None,
        tool_delimiter="tool",
        system_message_name=None,
        system_message=None,
        tool_output_format="json",
    )
    base = AgentPipeline.from_config(config)
    elements = list(base.elements)
    system_message, init_query, llm = elements[0], elements[1], elements[2]

    if mode == "guard_off":
        executor = ToolsExecutor()
        pipeline = AgentPipeline([
            system_message,
            init_query,
            llm,
            ToolsExecutionLoop([executor, llm]),
        ])
        pipeline.name = f"local-{model_id}-clean-guard-off"
    elif mode == "always_block":
        executor = AlwaysBlockToolsExecutor()
        pipeline = AgentPipeline([
            system_message,
            init_query,
            llm,
            ToolsExecutionLoop([executor, llm]),
        ])
        pipeline.name = f"local-{model_id}-always-block"
    elif mode == "argument_gated":
        executor = ArgumentGatedToolsExecutor()
        pipeline = AgentPipeline([
            system_message,
            init_query,
            p3.GBSRResetState(),
            llm,
            ToolsExecutionLoop([executor, llm]),
        ])
        pipeline.name = f"local-{model_id}-arg-gated"
    else: # guard_on (Session-Taint GBSR)
        executor = p3.GBSRToolsExecutor(
            sensitive_substrings=["email", "inbox", "message", "account", "bank", "private", "secret", "credential", "transaction", "contact", "file", "document"],
            untrusted_substrings=["browser", "web", "search", "fetch", "email", "inbox", "message", "file", "document", "calendar", "ticket", "transaction"],
            risky_action_substrings=[],
            block_risky_after_sensitive=True,
            block_risky_after_untrusted=True,
        )
        trace_path = logdir / "gbsr_trace.jsonl"
        pipeline = AgentPipeline([
            system_message,
            init_query,
            p3.GBSRResetState(),
            llm,
            ToolsExecutionLoop([executor, llm]),
            p3.GBSRFinalOutputGuard(trace_path, block_sensitive_final_output=True),
        ])
        pipeline.name = f"local-{model_id}-clean-guard-on"

    return pipeline
