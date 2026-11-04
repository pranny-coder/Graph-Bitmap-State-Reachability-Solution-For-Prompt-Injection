import pytest

# These checks execute AgentDojo pipeline components (and one real simulated
# inbox flow), so they cannot run when the optional benchmark is absent.
pytest.importorskip("agentdojo", reason="requires optional AgentDojo integration")

from phase3_agentdojo_gbsr import (
    GBSRToolsExecutor,
    _add_graph_node,
    _new_state,
)


def test_blocks_send_email_after_untrusted_context():
    state = _new_state()

    # Simulate an untrusted email field entering the context graph.
    _add_graph_node(
        state,
        node_type="data_field",
        name="injected_email.body",
        direct_sensitive=0,
        direct_influence=1,
        parents=[],
    )

    executor = GBSRToolsExecutor(
        sensitive_substrings=["email", "inbox"],
        untrusted_substrings=["email", "inbox"],
        risky_action_substrings=["send", "delete", "transfer"],
        block_risky_after_sensitive=True,
        block_risky_after_untrusted=True,
    )

    proposed_call = {
        "function": "send_email",
        "args": {
            "recipients": ["mark.black-2134@gmail.com"],
            "subject": "Diagnostic test",
            "body": "synthetic test payload",
        },
        "id": "call-gbsr-test",
        "placeholder_args": None,
    }

    assistant_message = {
        "role": "assistant",
        "tool_calls": [proposed_call],
    }

    _, _, _, messages, extra_args = executor.query(
        query=None,
        runtime=None,
        env=None,
        messages=[assistant_message],
        extra_args={"gbsr_state": state},
    )

    assert extra_args["gbsr_state"]["blocked_tool_calls"] == 1
    assert any(
        event["event"] == "tool_blocked"
        and event["tool"] == "send_email"
        for event in state["events"]
    )
    assert len(messages) == 2
    assert "blocked" in str(messages[-1].get("error", "")).lower()


def test_real_inbox_output_taints_and_blocks_followup_email():
    from agentdojo.task_suite.load_suites import get_suite
    from agentdojo.functions_runtime import FunctionsRuntime

    suite = get_suite("v1.2.2", "workspace")
    env = suite.load_and_inject_default_environment({})
    runtime = FunctionsRuntime(suite.tools)

    executor = GBSRToolsExecutor(
        sensitive_substrings=["email", "inbox"],
        untrusted_substrings=["email", "inbox"],
        risky_action_substrings=["send", "delete", "transfer"],
        block_risky_after_sensitive=True,
        block_risky_after_untrusted=True,
    )
    state = _new_state()

    # Step 1: Execute the real simulated inbox-reading tool.
    inbox_call = {
        "function": "get_unread_emails",
        "args": {},
        "id": "call-inbox-integration",
        "placeholder_args": None,
    }

    _, _, _, _, extra_args = executor.query(
        query=None,
        runtime=runtime,
        env=env,
        messages=[{
            "role": "assistant",
            "tool_calls": [inbox_call],
        }],
        extra_args={"gbsr_state": state},
    )

    state = extra_args["gbsr_state"]

    assert state["influence_mask"] != 0, (
        "Inbox output did not establish untrusted influence."
    )
    assert state["sensitive_mask"] != 0, (
        "Inbox output did not establish sensitive labels."
    )

    # Step 2: Propose a risky email action using the same session state.
    send_call = {
        "function": "send_email",
        "args": {
            "recipients": ["mark.black-2134@gmail.com"],
            "subject": "Guard integration probe",
            "body": "Synthetic test payload; must not be sent.",
        },
        "id": "call-send-integration",
        "placeholder_args": None,
    }

    _, _, _, returned_messages, extra_args = executor.query(
        query=None,
        runtime=runtime,
        env=env,
        messages=[{
            "role": "assistant",
            "tool_calls": [send_call],
        }],
        extra_args={"gbsr_state": state},
    )

    state = extra_args["gbsr_state"]

    assert state["blocked_tool_calls"] == 1
    assert any(
        event.get("event") == "tool_blocked"
        and event.get("tool") == "send_email"
        for event in state["events"]
    )

    # The synthetic email must not exist in the simulated sent mailbox.
    sent = env.inbox.search_emails(
        "Guard integration probe",
        sender=env.inbox.account_email,
    )
    assert len(sent) == 0, "Blocked email unexpectedly exists in the mailbox."

    assert "blocked" in str(returned_messages[-1].get("error", "")).lower()
