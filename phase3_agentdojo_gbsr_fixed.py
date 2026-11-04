"""GBSR adapter for the AgentDojo benchmark (optional Phase 3 integration).

This adapter uses conservative tool-output labeling based on configurable tool-name
substrings. If a tool name suggests confidential data, its result gets a sensitive
bit; if it suggests externally controlled content, its result gets an untrusted bit.
The context labels are unioned across observed tool results. Thus this is an
instrumented conservative baseline, not exact semantic provenance. The policy may
overblock legitimate tasks; utility results must be reported next to security.

Ollama OpenAI-compatible setup:
  export OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1
  export OPENAI_COMPATIBLE_API_KEY=ollama
  python3 phase3_agentdojo_gbsr.py --suite workspace --model openai-compatible \
    --model-id llama3.2:latest --attack tool_knowledge --user-tasks user_task_0 \
    --injection-tasks injection_task_0 --force-rerun

AgentDojo's APIs evolve. This module imports the documented BasePipelineElement,
ToolsExecutionLoop, TaskSuite loader and benchmark functions. Confirm `--help` and
suite/task IDs against your installed version before running a large evaluation.
"""
from __future__ import annotations
import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Ensure local venv site-packages is searched first
_venv_site = Path(__file__).resolve().parent / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if _venv_site.is_dir() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))


def _matches(name: str, substrings: list[str]) -> bool:
    low = name.casefold()
    return any(s.casefold() in low for s in substrings if s)


def _field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _new_state() -> dict[str, Any]:
    return {
        "sensitive_source_bits": {}, "untrusted_source_bits": {},
        "sensitive_mask": 0, "influence_mask": 0,
        "nodes": [], "events": [], "next_node": 0, "context_node": None,
        "blocked_tool_calls": 0, "blocked_final_outputs": 0,
    }


def _get_state(extra_args: dict) -> dict[str, Any]:
    if "gbsr_state" not in extra_args:
        extra_args["gbsr_state"] = _new_state()
    return extra_args["gbsr_state"]


def _source_bit(source_map: dict[str, int], name: str) -> int:
    if name not in source_map:
        source_map[name] = 1 << len(source_map)
    return source_map[name]


def _safe_shape(value: Any, depth: int = 0) -> Any:
    """Describe result structure without recording field values."""
    if depth >= 4:
        return {"type": type(value).__name__}

    if value is None:
        return {"type": "NoneType"}

    if hasattr(value, "model_dump"):
        try:
            return _safe_shape(value.model_dump(), depth + 1)
        except Exception:
            return {"type": type(value).__name__}

    if isinstance(value, dict):
        return {
            str(key): _safe_shape(item, depth + 1)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return {
            "type": type(value).__name__,
            "length": len(value),
            "item_shape": (
                _safe_shape(value[0], depth + 1) if value else None
            ),
        }

    if isinstance(value, str):
        return {"type": "str", "length": len(value)}

    if isinstance(value, (int, float, bool)):
        return {"type": type(value).__name__}

    if hasattr(value, "__dict__"):
        try:
            return _safe_shape(vars(value), depth + 1)
        except Exception:
            pass

    return {"type": type(value).__name__}



def _plain_data(value: Any) -> Any:
    """Convert supported structured tool results to plain Python data."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            value = dump(mode="python")
        except Exception:
            pass

    if isinstance(value, dict):
        return {str(k): _plain_data(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_plain_data(v) for v in value]

    return value


def _iter_leaf_fields(value: Any, path: str = "$"):
    """Yield (field path, value) pairs without logging the values."""
    if isinstance(value, dict) and value:
        for key, child in value.items():
            child_path = f"{path}.{key}" if path != "$" else str(key)
            yield from _iter_leaf_fields(child, child_path)
    elif isinstance(value, (list, tuple)) and value:
        for index, child in enumerate(value):
            yield from _iter_leaf_fields(child, f"{path}[{index}]")
    else:
        yield path, value


def _field_is_sensitive(
    tool_name: str,
    field_path: str,
    value: Any,
    sensitive_tool_substrings: list[str],
) -> bool:
    """Apply explicit field-name and sensitive-source policy heuristics."""
    normalized = (
        field_path.lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace(".", " ")
        .replace("[", " ")
        .replace("]", " ")
    )
    tokens = set(normalized.split())

    secret_terms = {
        "password", "passcode", "secret", "credential",
        "token", "otp", "pin", "ssn", "iban",
    }
    secret_phrases = (
        "security code",
        "verification code",
        "api key",
        "private key",
        "access token",
        "account number",
        "bank account",
        "social security",
    )

    if tokens & secret_terms:
        return True
    if any(phrase in normalized for phrase in secret_phrases):
        return True

    # Treat message bodies/content as sensitive when the configured source
    # policy classifies the originating tool as a sensitive data source.
    leaf = field_path.rsplit(".", 1)[-1]
    leaf = leaf.split("[", 1)[0].lower()

    private_content_fields = {
        "body", "content", "text", "message", "snippet"
    }

    return (
        isinstance(value, str)
        and _matches(tool_name, sensitive_tool_substrings)
        and (field_path == "$" or leaf in private_content_fields)
    )


def _add_graph_node(state: dict[str, Any], node_type: str, name: str,
                    direct_sensitive: int, direct_influence: int,
                    parents: list[int] | None = None) -> int:
    node_id = state["next_node"]
    state["next_node"] += 1

    # An omitted parent list means the current context node is the parent.
    # An explicit empty list means this node has no dependencies.
    node_parents = (
        list(parents)
        if parents is not None
        else ([state["context_node"]] if state["context_node"] is not None else [])
    )

    nodes_by_id = {int(node["id"]): node for node in state["nodes"]}
    parent_sensitive = 0
    parent_influence = 0

    for parent_id in node_parents:
        if parent_id not in nodes_by_id:
            raise ValueError(
                f"Unknown parent node {parent_id!r} for new node {node_id}"
            )
        parent = nodes_by_id[parent_id]
        parent_sensitive |= int(parent["sensitive_mask"])
        parent_influence |= int(parent["influence_mask"])

    # Node-local labels come only from direct labels and graph ancestors.
    node = {
        "id": node_id,
        "type": node_type,
        "name": name,
        "parents": node_parents,
        "direct_sensitive_mask": direct_sensitive,
        "direct_influence_mask": direct_influence,
        "sensitive_mask": parent_sensitive | direct_sensitive,
        "influence_mask": parent_influence | direct_influence,
    }

    state["nodes"].append(node)

    # Keep separate session-wide unions for conservative enforcement.
    state["sensitive_mask"] |= node["sensitive_mask"]
    state["influence_mask"] |= node["influence_mask"]
    state["context_node"] = node_id
    return node_id


# Imports are optional so Phase 2A/2B remain usable without AgentDojo installed.
try:
    from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
    from agentdojo.agent_pipeline.llms.openai_llm import (
        OpenAILLM,
        _function_to_openai,
        _message_to_openai,
        _openai_to_assistant_message,
    )
    from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
    from agentdojo.agent_pipeline.tool_execution import (
        ToolsExecutor, ToolsExecutionLoop, tool_result_to_str, is_string_list,
    )
    from agentdojo.agent_pipeline.llms.google_llm import EMPTY_FUNCTION_NAME
    from agentdojo.types import ChatToolResultMessage, text_content_block_from_string
    from agentdojo.task_suite.load_suites import get_suite
    from agentdojo.attacks.attack_registry import load_attack
    from agentdojo.benchmark import benchmark_suite_with_injections, benchmark_suite_without_injections
    from agentdojo.logging import OutputLogger
except ImportError as exc:  # pragma: no cover - tested only when installed
    _AGENTDOJO_IMPORT_ERROR = str(exc)
    AgentPipeline = PipelineConfig = BasePipelineElement = OpenAILLM = None  # type: ignore
    ToolsExecutor = ToolsExecutionLoop = None  # type: ignore
    tool_result_to_str = is_string_list = EMPTY_FUNCTION_NAME = None  # type: ignore
    ChatToolResultMessage = text_content_block_from_string = None  # type: ignore
    get_suite = load_attack = benchmark_suite_with_injections = benchmark_suite_without_injections = None  # type: ignore
    _function_to_openai = _message_to_openai = _openai_to_assistant_message = None  # type: ignore
else:
    _AGENTDOJO_IMPORT_ERROR = None


if BasePipelineElement is not None:
    class BoundedOpenAILLM(OpenAILLM):
        """OpenAI-compatible local-model adapter with a bounded completion size.

        AgentDojo's stock OpenAI adapter omits ``max_tokens``.  That is a
        reasonable cloud-provider default, but local Ollama models can continue
        generating well past a complete tool call and make paired evaluations
        impractically slow.  The bound applies only to model generation; it does
        not alter GBSR labels or enforcement.
        """

        def __init__(self, client, model: str, max_output_tokens: int,
                     temperature: float | None = 0.0, no_think: bool = False):
            super().__init__(client, model, temperature=temperature)
            self.max_output_tokens = max_output_tokens
            self.no_think = no_think

        def query(self, query, runtime, env=None, messages=(), extra_args=None):
            openai_messages = [
                _message_to_openai(message, self.model) for message in messages
            ]
            openai_tools = [
                _function_to_openai(tool) for tool in runtime.functions.values()
            ]
            request_kwargs = {}
            if self.no_think:
                # Ollama exposes Qwen3's reasoning toggle through its
                # OpenAI-compatible endpoint.  Without this, Qwen may spend
                # the whole bounded completion budget in hidden reasoning and
                # never emit the required tool call.
                request_kwargs["extra_body"] = {"think": False}
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=openai_messages,
                tools=openai_tools or None,
                tool_choice="auto" if openai_tools else None,
                temperature=self.temperature,
                max_tokens=self.max_output_tokens,
                **request_kwargs,
            )

            # Ollama/Qwen3 can sometimes exhaust the bounded generation budget
            # while returning neither visible text nor a tool call. Retry that
            # specific condition once with a larger budget. Do not retry valid
            # tool calls, normal text responses, or other stop reasons.
            choices = getattr(completion, "choices", None) or []
            if not choices:
                raise RuntimeError("OpenAI-compatible endpoint returned no completion choices.")

            first_choice = choices[0]
            first_message = getattr(first_choice, "message", None)
            first_content = getattr(first_message, "content", None) or ""
            first_tool_calls = getattr(first_message, "tool_calls", None) or []
            if (
                getattr(first_choice, "finish_reason", None) == "length"
                and not first_content.strip()
                and not first_tool_calls
            ):
                retry_tokens = min(max(4096, self.max_output_tokens * 4), 8192)
                print(
                    "[LLM RETRY] empty response was length-truncated; "
                    f"retrying once with max_tokens={retry_tokens}"
                )
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=openai_messages,
                    tools=openai_tools or None,
                    tool_choice="auto" if openai_tools else None,
                    temperature=self.temperature,
                    max_tokens=retry_tokens,
                    **request_kwargs,
                )
                choices = getattr(completion, "choices", None) or []
                if not choices:
                    raise RuntimeError("Retry to OpenAI-compatible endpoint returned no completion choices.")

            # Print response metadata only; never print email bodies, calendar
            # descriptions, model text, or other potentially sensitive content.
            choice = choices[0]
            message = getattr(choice, "message", None)
            content = getattr(message, "content", None) or ""
            tool_calls = getattr(message, "tool_calls", None) or []
            tool_names = []
            for call in tool_calls:
                function = getattr(call, "function", None)
                tool_names.append(getattr(function, "name", None) or "<unnamed>")
            usage = getattr(completion, "usage", None)
            print(
                "[LLM DEBUG]",
                "finish_reason=", repr(getattr(choice, "finish_reason", None)),
                "content_len=", len(content),
                "tool_calls=", tool_names,
                "completion_tokens=", getattr(usage, "completion_tokens", None),
            )

            output = _openai_to_assistant_message(completion.choices[0].message)
            return query, runtime, env, [*messages, output], (
                {} if extra_args is None else extra_args
            )


    class GBSRResetState(BasePipelineElement):
        def query(self, query, runtime, env=None, messages=(), extra_args=None):
            extra_args = {} if extra_args is None else extra_args
            extra_args["gbsr_state"] = _new_state()
            return query, runtime, env, messages, extra_args


    # Explicit suite tool classifications (Default-Deny for mutating tools)
    KNOWN_READ_ONLY_TOOLS = {
        "get_iban",
        "get_balance",
        "get_most_recent_transactions",
        "get_scheduled_transactions",
        "get_user_info",
        "read_file",
    }

    KNOWN_MUTATING_TOOLS = {
        "send_money",
        "schedule_transaction",
        "update_scheduled_transaction",
        "update_password",
        "update_user_info",
    }


    def is_state_mutating_tool(tool_name: str, custom_risky_substrings: list[str] | None = None) -> bool:
        """Classify tool using default-deny: known read-only tools are safe; all others mutate state."""
        name_low = tool_name.casefold()
        if custom_risky_substrings and _matches(name_low, custom_risky_substrings):
            return True
        if name_low in KNOWN_READ_ONLY_TOOLS:
            return False
        # Known mutating tools or any unknown tool defaults to mutating (fail-closed)
        return True


    class GBSRToolsExecutor(ToolsExecutor):
        def __init__(self, sensitive_substrings: list[str], untrusted_substrings: list[str],
                     risky_action_substrings: list[str], block_risky_after_sensitive: bool = True,
                     block_risky_after_untrusted: bool = True):
            super().__init__()
            self.sensitive_substrings = sensitive_substrings
            self.untrusted_substrings = untrusted_substrings
            self.risky_action_substrings = risky_action_substrings
            self.block_risky_after_sensitive = block_risky_after_sensitive
            self.block_risky_after_untrusted = block_risky_after_untrusted

        def query(self, query, runtime, env=None, messages=(), extra_args=None):
            extra_args = {} if extra_args is None else extra_args
            if not messages or _field(messages[-1], "role") != "assistant":
                return query, runtime, env, messages, extra_args
            tool_calls = _field(messages[-1], "tool_calls")
            if not tool_calls:
                return query, runtime, env, messages, extra_args

            state = _get_state(extra_args)
            results = []
            # All calls in this assistant message were proposed from the same
            # context, so gate each using the masks existing before this batch.
            prior_s = int(state["sensitive_mask"])
            prior_i = int(state["influence_mask"])
            prior_context_id = state["context_node"]
            action_parents = (
                [int(prior_context_id)]
                if prior_context_id is not None else []
            )
            batch_output_ids = []
            for call in tool_calls:
                name = str(_field(call, "function", ""))
                call_id = _field(call, "id")
                args = dict(_field(call, "args", {}) or {})

                action_node_id = _add_graph_node(
                    state, "proposed_action", name, 0, 0,
                    parents=action_parents,
                )
                state["events"].append({
                    "event": "action_proposed",
                    "tool": name,
                    "action_node_id": action_node_id,
                    "dependency_node_ids": action_parents,
                })

                is_risky = is_state_mutating_tool(name, self.risky_action_substrings)
                sensitive_conflict = self.block_risky_after_sensitive and prior_s != 0
                influence_conflict = self.block_risky_after_untrusted and prior_i != 0
                if is_risky and (sensitive_conflict or influence_conflict):
                    reason_bits = []
                    if sensitive_conflict:
                        reason_bits.append(f"sensitive_mask={prior_s}")
                    if influence_conflict:
                        reason_bits.append(f"untrusted_influence_mask={prior_i}")
                    reason = "GBSR blocked risky tool call after tainted context (" + ", ".join(reason_bits) + ")."
                    state["blocked_tool_calls"] += 1
                    state["events"].append({"event": "tool_blocked", "tool": name,
                                             "reason": reason, "context_sensitive_mask": prior_s,
                                             "context_influence_mask": prior_i})
                    results.append(ChatToolResultMessage(
                        role="tool", content=[text_content_block_from_string(reason)],
                        tool_call_id=call_id, tool_call=call, error=reason,
                    ))
                    # An action node records the attempted, but not executed, operation.
                    blocked_node_id = _add_graph_node(
                        state, "blocked_action", name, 0, 0,
                        parents=[action_node_id],
                    )
                    batch_output_ids.append(blocked_node_id)
                    continue

                if name == EMPTY_FUNCTION_NAME or name not in (tool.name for tool in runtime.functions.values()):
                    tool_result, error = "", f"Invalid tool {name!r} provided."
                else:
                    for arg_k, arg_v in list(args.items()):
                        if not isinstance(arg_v, str):
                            continue

                        value = arg_v.strip()
                        if value.casefold() in {"null", "none"}:
                            args[arg_k] = None
                            continue

                        # Local models sometimes serialize lists/dictionaries
                        # as strings instead of returning structured arguments.
                        if (
                            len(value) >= 2
                            and (value[0], value[-1]) in {("[", "]"), ("{", "}")}
                        ):
                            try:
                                parsed = json.loads(value)
                            except (json.JSONDecodeError, TypeError):
                                try:
                                    parsed = ast.literal_eval(value)
                                except (ValueError, SyntaxError):
                                    continue

                            if isinstance(parsed, (list, dict)):
                                args[arg_k] = parsed

                    tool_result, error = runtime.run_function(env, name, args)
                formatted = self.output_formatter(tool_result)

                is_action = _matches(name, self.risky_action_substrings)
                direct_s = 0
                direct_i = 0
                field_node_ids = []

                # Failed calls contain error information, not successful source
                # data. Do not classify error messages as confidential content.
                if error is None:
                    plain_result = _plain_data(tool_result)

                    for field_path, field_value in _iter_leaf_fields(plain_result):
                        field_sensitive = (
                            not is_action
                            and _field_is_sensitive(
                                name, field_path, field_value,
                                self.sensitive_substrings,
                            )
                        )
                        # Trust follows the configured source policy, not
                        # the Python type of the returned field.
                        field_untrusted = (
                            not is_action
                            and _matches(name, self.untrusted_substrings)
                        )

                        source_key = f"{name}@{action_node_id}:{field_path}"
                        field_s = (
                            _source_bit(state["sensitive_source_bits"], source_key)
                            if field_sensitive else 0
                        )
                        field_i = (
                            _source_bit(state["untrusted_source_bits"], source_key)
                            if field_untrusted else 0
                        )

                        direct_s |= field_s
                        direct_i |= field_i

                        field_node_id = _add_graph_node(
                            state, "data_field",
                            f"{name}:{field_path}",
                            field_s, field_i,
                            parents=[action_node_id],
                        )
                        field_node_ids.append(field_node_id)

                # The aggregate tool result joins field provenance. The fields
                # remain separate nodes for inspection and policy evaluation.
                result_parents = (
                    field_node_ids if field_node_ids else [action_node_id]
                )
                node_id = _add_graph_node(
                    state, "tool_result", name, 0, 0,
                    parents=result_parents,
                )
                batch_output_ids.append(node_id)
                state["events"].append({"event": "tool_result", "tool": name, "node_id": node_id,
                                         "direct_sensitive_mask": direct_s,
                                         "direct_influence_mask": direct_i,
                                         "context_sensitive_mask": state["sensitive_mask"],
                                         "context_influence_mask": state["influence_mask"],
                                         "error": error is not None, "tool_result_shape": _safe_shape(tool_result)})
                results.append(ChatToolResultMessage(
                    role="tool", content=[text_content_block_from_string(formatted)],
                    tool_call_id=call_id, tool_call=call, error=error,
                ))
            if batch_output_ids:
                context_node_id = _add_graph_node(
                    state, "context_aggregate", "assistant_visible_context",
                    0, 0, parents=batch_output_ids,
                )
                state["events"].append({
                    "event": "context_aggregate",
                    "node_id": context_node_id,
                    "dependency_node_ids": batch_output_ids,
                })

            return query, runtime, env, [*messages, *results], extra_args


    class GBSRFinalOutputGuard(BasePipelineElement):
        def __init__(self, trace_path: Path, block_sensitive_final_output: bool = True):
            self.trace_path = trace_path
            self.block_sensitive_final_output = block_sensitive_final_output

        def query(self, query, runtime, env=None, messages=(), extra_args=None):
            extra_args = {} if extra_args is None else extra_args
            state = _get_state(extra_args)
            updated_messages = list(messages)
            final_is_assistant = bool(updated_messages) and _field(updated_messages[-1], "role") == "assistant"
            is_tool_call = bool(final_is_assistant and _field(updated_messages[-1], "tool_calls"))
            blocked_final = bool(self.block_sensitive_final_output and state["sensitive_mask"] and final_is_assistant and not is_tool_call)
            if blocked_final:
                original = dict(updated_messages[-1])
                original["content"] = [text_content_block_from_string(
                    "I can't release this response because sensitive source data was present in the model context."
                )]
                original["tool_calls"] = None
                updated_messages[-1] = original
                state["blocked_final_outputs"] += 1
                state["events"].append({"event": "final_output_blocked",
                                         "context_sensitive_mask": state["sensitive_mask"],
                                         "context_influence_mask": state["influence_mask"]})
            final_node = _add_graph_node(state, "final_output", "assistant_final", 0, 0)
            state["events"].append({"event": "run_summary", "final_node_id": final_node,
                                     "sensitive_mask": state["sensitive_mask"],
                                     "influence_mask": state["influence_mask"],
                                     "blocked_tool_calls": state["blocked_tool_calls"],
                                     "blocked_final_outputs": state["blocked_final_outputs"],
                                     "label_strategy": "field-level graph provenance; tool-name source policy; conservative context union"})
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self.trace_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"events": state["events"], "nodes": state["nodes"]}, ensure_ascii=False) + "\n")
            return query, runtime, env, updated_messages, extra_args


def build_gbsr_pipeline(args):
    if AgentPipeline is None:
        raise RuntimeError(f"AgentDojo import failed: {_AGENTDOJO_IMPORT_ERROR}. Install with `python3 -m pip install agentdojo`.")

    # AgentDojo 0.1.35 does not list `openai-compatible` in ModelsEnum. Use
    # its documented custom-BasePipelineElement path instead of passing the
    # provider string through ModelsEnum. The OpenAI SDK talks to Ollama's
    # OpenAI-compatible /v1 endpoint.
    model_for_config = args.model
    if args.model == "openai-compatible":
        if OpenAILLM is None:
            raise RuntimeError("Could not import AgentDojo OpenAILLM. Check your AgentDojo installation.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Missing OpenAI SDK. Run `python3 -m pip install openai` in this venv.") from exc

        base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
        api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "ollama")
        if not args.model_id:
            raise RuntimeError("Pass --model-id with the exact model ID reported by Ollama.")
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=args.request_timeout,
        )
        custom_llm = BoundedOpenAILLM(
            client, args.model_id, args.max_output_tokens,
            no_think=args.ollama_no_think,
        )
        # AgentDojo 0.1.35's PipelineConfig path reads `.name` for custom
        # pipeline elements; OpenAILLM does not set it by default.
        custom_llm.name = args.model_id
        model_for_config = custom_llm

    config = PipelineConfig(
        llm=model_for_config,
        model_id=args.model_id,
        defense=None,
        tool_delimiter=args.tool_delimiter,
        system_message_name=args.system_message_name,
        system_message=args.system_message,
        tool_output_format="json",
    )
    base = AgentPipeline.from_config(config)
    elements = list(base.elements)
    if len(elements) < 3:
        raise RuntimeError(f"Unexpected AgentDojo pipeline layout with {len(elements)} elements; inspect installed API.")
    system_message, init_query, llm = elements[0], elements[1], elements[2]
    executor = GBSRToolsExecutor(
        sensitive_substrings=args.sensitive_tool_substrings,
        untrusted_substrings=args.untrusted_tool_substrings,
        risky_action_substrings=args.risky_action_substrings,
        block_risky_after_sensitive=not args.allow_risky_after_sensitive,
        block_risky_after_untrusted=not args.allow_risky_after_untrusted,
    )
    trace_path = Path(args.logdir) / "gbsr_trace.jsonl"
    pipeline = AgentPipeline([
        system_message,
        init_query,
        GBSRResetState(),
        llm,
        ToolsExecutionLoop([executor, llm]),
        GBSRFinalOutputGuard(trace_path, block_sensitive_final_output=not args.allow_sensitive_final_output),
    ])
    pipeline.name = f"local-{args.model_id or args.model}-gbsr"
    return pipeline


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--suite", default="workspace")
    p.add_argument("--benchmark-version", default="v1.2.2")
    p.add_argument("--model", default="openai-compatible")
    p.add_argument("--model-id", default="llama3.2:latest")
    p.add_argument("--max-output-tokens", type=int, default=512,
                   help="Maximum tokens per local-model completion")
    p.add_argument("--request-timeout", type=float, default=90.0,
                   help="Timeout in seconds for one local-model request")
    p.add_argument("--ollama-no-think", action="store_true",
                   help="Disable Qwen3 reasoning mode through Ollama's OpenAI-compatible API")
    p.add_argument("--tool-delimiter", default="tool")
    p.add_argument("--system-message-name", default=None)
    p.add_argument("--system-message", default=None)
    p.add_argument("--attack", default="tool_knowledge", help="Set empty string to run without injections")
    p.add_argument("--user-tasks", nargs="*", default=None)
    p.add_argument("--injection-tasks", nargs="*", default=None)
    p.add_argument("--logdir", type=Path, default=Path("runs_gbsr"))
    p.add_argument("--force-rerun", action="store_true")
    p.add_argument("--sensitive-tool-substrings", nargs="+",
                   default=["email", "inbox", "message", "account", "bank", "private", "secret", "credential", "transaction", "contact", "file", "document"])
    p.add_argument("--untrusted-tool-substrings", nargs="+",
                   default=["browser", "web", "search", "fetch", "email", "inbox", "message", "file", "document", "calendar", "ticket"])
    p.add_argument("--risky-action-substrings", nargs="+",
                   default=["send", "forward", "delete", "transfer", "pay", "post", "share", "write", "update", "create", "book", "execute", "move"])
    p.add_argument("--allow-risky-after-sensitive", action="store_true",
                   help="Diagnostic only: disable block on risky actions after sensitive source access")
    p.add_argument("--allow-risky-after-untrusted", action="store_true",
                   help="Diagnostic only: disable block on risky actions after untrusted tool outputs")
    p.add_argument("--allow-sensitive-final-output", action="store_true",
                   help="Diagnostic only: allow final response after sensitive tool outputs")
    args = p.parse_args()
    if AgentPipeline is None:
        p.error(f"AgentDojo is not installed/importable: {_AGENTDOJO_IMPORT_ERROR}. Run `python3 -m pip install agentdojo`.")
    from agentdojo.task_suite.load_suites import get_suite
    from agentdojo.attacks.attack_registry import load_attack
    from agentdojo.benchmark import benchmark_suite_with_injections, benchmark_suite_without_injections

    pipeline = build_gbsr_pipeline(args)
    suite = get_suite(args.benchmark_version, args.suite)
    args.logdir.mkdir(parents=True, exist_ok=True)
    with OutputLogger(str(args.logdir)):
        if args.attack:
            attack = load_attack(args.attack, suite, pipeline)
            result = benchmark_suite_with_injections(
                pipeline, suite, attack, logdir=args.logdir, force_rerun=args.force_rerun,
                user_tasks=args.user_tasks or None, injection_tasks=args.injection_tasks or None,
                verbose=True, benchmark_version=args.benchmark_version,
            )
        else:
            result = benchmark_suite_without_injections(
                pipeline, suite, logdir=args.logdir, force_rerun=args.force_rerun,
                user_tasks=args.user_tasks or None, benchmark_version=args.benchmark_version,
            )
    def avg(values):
        return 100.0 * sum(values) / len(values) if values else float("nan")
    print(f"\nSuite: {suite.name}; pipeline: {pipeline.name}")
    print(f"Utility: {avg(list(result['utility_results'].values())):.2f}%")
    if args.attack:
        print(f"Security: {avg(list(result['security_results'].values())):.2f}%")
        print(f"Injection task utility: {avg(list(result['injection_tasks_utility_results'].values())):.2f}%")
    print(f"GBSR traces: {args.logdir / 'gbsr_trace.jsonl'}")
    print("Labeling caveat: source labels are inferred from configured tool-name substrings and propagated conservatively across context; this is not exact semantic provenance.")

if __name__ == "__main__":
    main()